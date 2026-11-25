import asyncio
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

from .observer import Handle, Registry, Uncertain, Violation, observe

API_TIMEOUT = 10
ASSETS = Path(__file__).resolve().parent


class Scenario:
    def __init__(self, args, root, report):
        self.a, self.root, self.report = args, root, report
        self.reg = Registry(str(root / "w.sock"), report)
        self.proc = None
        self.prep = None
        self.prep_handle = None
        self.serial = 0
        self.thread = None
        self.used = set()
        self.births = set()
        home = str(root)
        self.env = dict(
            PATH=os.environ["PATH"],
            HOME=home,
            CODEX_HOME=home,
            XDG_CONFIG_HOME=home,
            XDG_DATA_HOME=home,
            XDG_CACHE_HOME=home,
            TMPDIR=home,
            LANG="C",
            NPM_CONFIG_USERCONFIG=os.devnull,
            NPM_CONFIG_GLOBALCONFIG=str(root / "empty-npmrc"),
            NPM_CONFIG_OFFLINE="true",
            OPENAI_BASE_URL="http://127.0.0.1:1/v1",
        )
        self.pending = None

    async def prepare(self):
        if self.a.launcher != "npm":
            return
        (self.root / "package.json").write_text(
            '{"name":"exit-trial","version":"0.0.0","private":true}'
        )
        # Copy the bin and set its mode explicitly for wheel installers.
        local = self.root / "wrapper"
        shutil.copytree(ASSETS / "wrapper", local)
        (local / "probe").chmod(0o755)
        self.prep = await asyncio.create_subprocess_exec(
            "npm",
            "install",
            "--offline",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--no-package-lock",
            "./wrapper",
            start_new_session=True,
            cwd=self.root,
            env=self.env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.prep_handle = Handle(self.prep.pid)
        if await asyncio.wait_for(self.prep.wait(), 10):
            raise Uncertain("Private offline npm installation failed")

    def config(self, gen):
        self.reg.generation = gen
        argv = [
            sys.executable,
            str(ASSETS / "fixture.py"),
            self.reg.path,
            self.reg.nonce,
            str(gen),
            self.a.policy,
        ]
        if self.a.launcher == "npm":
            argv = ["npm", "exec", "--offline", "--yes=false", "--", "local-exit-probe", *argv]
        command = shutil.which(argv[0])
        if not command:
            raise Uncertain("Launcher unavailable")
        params = dict(command=command, args=argv[1:], cwd=str(self.root))
        if self.a.driver == "codex":
            settings = "\n".join(
                f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in params.items()
            )
            config = (
                'model_provider = "localtest"\n[model_providers.localtest]\n'
                'name = "Local synthetic provider"\nbase_url = "http://127.0.0.1:1/v1"\n'
                'wire_api = "responses"\n[analytics]\nenabled = false\n'
                "[mcp_servers.exitprobe]\n" + settings + "\n"
            )
            tmp = self.root / "config.next"
            tmp.write_text(config)
            tmp.replace(self.root / "config.toml")
        return params

    async def send(self, data):
        self.proc.stdin.write(json.dumps(data).encode() + b"\n")
        await self.proc.stdin.drain()

    async def rpc(self, method, params):
        self.serial += 1
        self.reg.log("request", method=method, request=self.serial)
        async with asyncio.timeout(API_TIMEOUT):
            await self.send(dict(id=self.serial, method=method, params=params))
            while line := await self.proc.stdout.readline():
                msg = json.loads(line)
                if msg.get("id") == self.serial and "method" not in msg:
                    self.reg.log("response", method=method, request=self.serial)
                    if "error" in msg:
                        raise Uncertain(f"Driver rejected {method}")
                    return msg["result"]
                if "id" in msg and "method" in msg:
                    await self.send(
                        dict(id=msg["id"], error=dict(code=-32601, message="Unavailable"))
                    )
        raise Uncertain(f"Driver exited during {method}")

    async def tool_identity(self):
        before = self.reg.call_number
        if self.a.driver == "codex":
            result = await self.rpc(
                "mcpServer/tool/call",
                dict(threadId=self.thread, server="exitprobe", tool="who", arguments={}),
            )
        else:
            result = await self.rpc("who", {})
        if result.get("isError"):
            if any(gen == self.reg.generation for gen, _ in self.reg.rejected):
                raise Uncertain("Fixture rejected the MCP handshake")
            raise Uncertain("Fixture tool call returned an error")
        witness = json.loads(result["content"][0]["text"])
        key = witness["generation"], witness["instance"]
        if (
            type(key[0]) is not int
            or not isinstance(key[1], str)
            or key not in self.reg.initialized
            or self.reg.last_call.get(key, 0) <= before
        ):
            raise Uncertain("Tool reply could not be bound to an initialized fixture call")
        # Child registration may still be in flight during the first call.
        async with asyncio.timeout(2):
            while len(self.reg.pairs[key]) != 2:
                await asyncio.sleep(0.02)
        if any(h.state() != "active" for h in self.reg.pairs[key].values()):
            raise Uncertain("Baseline fixture pair was not active")
        return key

    async def call(self, generation, expected=None):
        key = await self.tool_identity()
        if key[0] != generation or (expected and expected != key):
            repeated = await self.tool_identity()
            if repeated == key:
                raise Violation("Two calls returned the same unexpected verified fixture")
            raise Uncertain("Tool routing changed between verification calls")

        if expected is None and key[1] in self.used:
            raise Violation("Successor reused a fixture UUID")
        self.used.add(key[1])
        server = self.reg.pairs[key]["server"].birth
        birth = server["pid"], server["start_ticks"]
        if expected is None and birth in self.births:
            raise Violation("Successor reused a server birth identity")
        self.births.add(birth)
        self.reg.log("who", generation=generation, instance=key[1])
        print(f"Ready generation {generation} instance {key[1]}", flush=True)
        return key

    async def discover(self):
        res = await self.rpc(
            "mcpServerStatus/list",
            dict(threadId=self.thread, serverName="exitprobe", detail="full"),
        )
        if not any("who" in s["tools"] and s["name"] == "exitprobe" for s in res["data"]):
            raise Uncertain("Thread did not discover the fixture tool")

    def watch(self, trigger, retired, successor=None):
        self.pending = asyncio.create_task(
            observe(self.reg, retired, self.a.grace, trigger, time.monotonic(), successor)
        )

    async def run(self):
        # Refuse to launch if birth-bound signaling is unavailable.
        probe = Handle(os.getpid())
        try:
            signal.pidfd_send_signal(probe.fd, 0)
        finally:
            probe.close()
        await self.prepare()
        params = self.config(1)
        sdk = self.a.driver == "sdk"
        cmd = ["node", str(self.a.sdk_dir / "driver.mjs")] if sdk else ["codex", "app-server"]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.root,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=2**22,
        )
        self.reg.host = Handle(self.proc.pid)
        self.reg.log("driver_birth", identity=self.reg.host.birth)
        if sdk:
            await self.rpc("connect", params)
        else:
            await self.rpc(
                "initialize",
                dict(
                    clientInfo=dict(name="exit-witness", version="0.1.0"),
                    capabilities=dict(experimentalApi=True),
                ),
            )
            await self.send(dict(method="initialized"))
            effective = await self.rpc("config/read", dict(cwd=str(self.root), includeLayers=False))
            servers = effective["config"].get("mcp_servers", {})
            if set(servers) != {"exitprobe"} or any(
                servers["exitprobe"].get(k) != v for k, v in params.items()
            ):
                raise Uncertain("Effective configuration contains unexpected MCP registrations")
            result = await self.rpc(
                "thread/start",
                dict(
                    cwd=str(self.root), ephemeral=True, approvalPolicy="never", sandbox="read-only"
                ),
            )
            self.thread = result["thread"]["id"]
            self.reg.log("thread_started", thread=self.thread)
            await self.discover()
        await self.call(1)
        for gen in range(2, 2 + self.a.reconnects):
            retired = self.reg.roles()
            params = self.config(gen)
            if sdk:
                await self.rpc("close", {})
                self.watch("SDK client.close", retired, gen)
            else:
                await self.rpc("config/mcpServer/reload", {})
                self.watch("configuration reconnect", retired, gen)
            try:
                if sdk:
                    await self.rpc("connect", params)
                else:
                    await self.discover()
                key = await self.call(gen)
            except Exception as exc:
                self.reg.log(
                    "successor_issue", generation=gen, request=self.serial, cause=type(exc).__name__
                )
                # Keep sampling to the original deadline, even on an early rejection.
                await asyncio.gather(self.pending, return_exceptions=True)
                raise
            await self.pending
            await self.call(gen, key)
        if sdk:
            await self.rpc("close", {})
            self.watch("explicit SDK disconnect", self.reg.roles())
            await self.pending
        if self.reg.host.state() != "active":
            raise Uncertain("Driver was unavailable before its final lifecycle trigger")
        if self.a.exit == "interrupt":
            self.reg.host.send(signal.SIGTERM)
            trigger = "host SIGTERM"
        else:
            self.proc.stdin.close()
            trigger = "driver input EOF"
        self.reg.log("exit_requested", trigger=trigger)
        self.watch(trigger, {**self.reg.roles(), "driver": self.reg.host})

        async def wait_exit():
            status = await asyncio.wait_for(self.proc.wait(), 10)
            self.reg.log("driver_exit", status=status)
            return status

        exit_task = asyncio.create_task(wait_exit())
        try:
            await self.pending
            status = await exit_task
        finally:
            if not exit_task.done():
                exit_task.cancel()
            await asyncio.gather(exit_task, return_exceptions=True)
        if self.a.exit == "eof" and status != 0:
            raise Uncertain("Driver exited with nonzero status after EOF")
        if self.reg.rejected:
            raise Uncertain("Fixture rejected the MCP handshake")
        if self.reg.problem or len(self.reg.pairs) != len(self.used):
            raise Uncertain("Incomplete registration coverage")

    def handles(self):
        rows = self.reg.roles()
        if self.reg.host:
            rows["driver"] = self.reg.host
        if self.prep_handle:
            rows["npm_install"] = self.prep_handle
        return rows

    def remaining(self):
        states = {role: handle.state() for role, handle in self.handles().items()}
        return {role: state for role, state in states.items() if state != "gone"}

    async def cleanup(self):
        if self.pending:
            if not self.pending.done():
                self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
        if self.proc and self.proc.stdin:
            self.proc.stdin.close()

        for sig in (signal.SIGTERM, signal.SIGKILL):
            until = time.monotonic() + 2
            signaled = set()
            while True:
                for role, handle in self.handles().items():
                    if handle not in signaled and handle.state() == "active":
                        handle.send(sig)
                        self.reg.log("cleanup_signal", role=role, signal=sig.name)
                        signaled.add(handle)
                if not self.remaining():
                    break
                if time.monotonic() >= until:
                    break
                await asyncio.sleep(0.05)
        for child in (self.proc, self.prep):
            if child:
                await asyncio.wait_for(child.wait(), 0.5)
        await self.reg.close()
        return self.remaining()
