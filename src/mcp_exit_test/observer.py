"""Linux birth identity and credential-bound fixture registry."""

import asyncio
import hashlib
import json
import os
import select
import signal
import socket
import struct
import time
import uuid
from pathlib import Path


from .handshake import SUPPORTED_PROTOCOLS


class Uncertain(Exception):
    """The scoped contract could not be assessed."""


class Violation(Exception):
    """An observed identity contradicted its lifecycle contract."""


def stat(pid):
    # comm is parenthesized and may itself contain spaces and parentheses.
    fields = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b") ", 1)[1].split()
    return dict(
        pid=pid,
        state=fields[0].decode(),
        ppid=int(fields[1]),
        pgid=int(fields[2]),
        sid=int(fields[3]),
        start_ticks=int(fields[19]),
    )


class Handle:
    def __init__(self, pid):
        first = stat(pid)
        self.fd = os.pidfd_open(pid)
        try:
            self.poller = select.poll()
            self.poller.register(self.fd, select.POLLIN)
            self.birth = stat(pid)
            if first["start_ticks"] != self.birth["start_ticks"]:
                raise Uncertain("Identity changed during registration")
        except BaseException:
            os.close(self.fd)
            raise

    def state(self):
        # A pidfd hangup proves reaping, including same-tick numeric PID reuse.
        for _, flags in self.poller.poll(0):
            if flags & select.POLLHUP:
                return "gone"
            if flags & select.POLLIN:
                # Older pidfds signal exit for both zombies and reaped tasks.
                # The kernel reports Pid: -1 once this handle has no task.
                try:
                    lines = Path(f"/proc/self/fdinfo/{self.fd}").read_bytes().splitlines()
                except OSError:
                    break
                if any(line.split() == [b"Pid:", b"-1"] for line in lines):
                    return "gone"
        try:
            now = stat(self.birth["pid"])
        except (OSError, ValueError, IndexError):
            return "unknown"
        if now["start_ticks"] != self.birth["start_ticks"]:
            return "gone"
        if now["state"] == "Z":
            return "zombie"
        return "active" if now["state"] in "RSDTtI" else "unknown"

    def send(self, sig):
        if self.state() == "active":
            try:
                signal.pidfd_send_signal(self.fd, sig)
            except ProcessLookupError:
                pass

    def close(self):
        os.close(self.fd)


class Registry:
    def __init__(self, path, report):
        self.path = path
        self.nonce = uuid.uuid4().hex
        self.host = None
        self.generation = 0
        self.pairs = {}
        self.rejected = set()
        self.negotiated = {}
        self.initialized = set()
        self.call_number = 0
        self.last_call = {}
        self.report = report
        self.start = time.monotonic()
        self.problem = False
        self.listeners = set()
        self.listener = None

    def log(self, event, **data):
        self.report["timeline"].append(
            dict(t=round(time.monotonic() - self.start, 5), event=event, **data)
        )

    async def open(self):
        boot = Path("/proc/sys/kernel/random/boot_id").read_bytes()
        self.report["boot_tag"] = hashlib.sha256(self.nonce.encode() + boot).hexdigest()[:16]
        self.listener = await asyncio.start_unix_server(self.receive, self.path, limit=4096)

    def ancestry(self, pid):
        chain = []
        while pid > 1 and len(chain) < 32:
            item = stat(pid)
            if self.host and pid == self.host.birth["pid"]:
                if item["start_ticks"] == self.host.birth["start_ticks"]:
                    return chain
                break
            chain.append(item)
            pid = item["ppid"]
        raise Uncertain("Fixture ancestry could not be bound to driver")

    async def receive(self, reader, writer):
        task = asyncio.current_task()
        self.listeners.add(task)
        handle = None
        try:
            peer = writer.get_extra_info("socket").getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, 12
            )
            pid, uid, _ = struct.unpack("3i", peer)
            msg = json.loads(await asyncio.wait_for(reader.readline(), 2))
            if uid != os.getuid() or msg["nonce"] != self.nonce:
                return
            kind, instance, gen = msg["kind"], str(uuid.UUID(msg["instance"])), msg["generation"]
            key = (gen, instance)
            pair = self.pairs.get(key, {})
            if kind in ("server", "child"):
                if gen != self.generation or kind in pair:
                    raise Uncertain("Unexpected fixture registration")
                handle = Handle(pid)
                chain = self.ancestry(handle.birth["ppid"])
                if kind == "child" and (
                    not chain
                    or chain[0]["pid"] != pair["server"].birth["pid"]
                    or chain[0]["start_ticks"] != pair["server"].birth["start_ticks"]
                ):
                    raise Uncertain("Child registration has an unexpected parent")
                pair[kind] = handle
                self.pairs[key] = pair
                handle = None
                self.log(
                    "birth",
                    instance=instance,
                    generation=gen,
                    role=kind,
                    identity=pair[kind].birth,
                    ancestry=chain if kind == "server" else [],
                )
            else:
                server = pair["server"]
                if pid != server.birth["pid"] or server.state() != "active":
                    raise Uncertain("Fixture event has an unexpected sender")
                protocol = msg.get("protocol")
                details = {}
                if kind == "handshake_rejected":
                    method = msg.get("method")
                    if method not in ("initialize", "notifications/initialized"):
                        raise Uncertain("Unknown handshake method")
                    self.rejected.add(key)
                    self.initialized.discard(key)
                    details["method"] = method
                elif kind == "initialize_response":
                    if (
                        key in self.rejected
                        or key in self.negotiated
                        or protocol not in SUPPORTED_PROTOCOLS
                    ):
                        raise Uncertain("Invalid initialize response evidence")
                    self.negotiated[key] = protocol
                elif kind == "initialized":
                    if (
                        key in self.rejected
                        or key not in self.negotiated
                        or protocol != self.negotiated[key]
                        or key in self.initialized
                    ):
                        raise Uncertain("Initialized notification lacks a matching response")
                    self.initialized.add(key)
                elif kind == "who_called":
                    if key not in self.initialized:
                        raise Uncertain("Tool call before handshake completion")
                    self.call_number += 1
                    self.last_call[key] = self.call_number
                elif kind != "eof":
                    raise Uncertain("Unexpected fixture event")
                self.log(kind, instance=instance, generation=gen, protocol=protocol, **details)
            writer.write(b"+")
            await writer.drain()
        except (Exception, asyncio.CancelledError):
            self.problem = True
        finally:
            if handle:
                handle.close()
            writer.close()
            self.listeners.discard(task)

    def roles(self):
        return {f"{g}/{u}/{r}": h for (g, u), pair in self.pairs.items() for r, h in pair.items()}

    async def close(self):
        if self.listener:
            self.listener.close()
            await self.listener.wait_closed()
        if self.listeners:
            await asyncio.gather(*list(self.listeners), return_exceptions=True)


async def observe(reg, retired, grace, trigger, start, successor=None):
    """Sample concurrently with successor setup. Retain the deadline evidence."""
    deadline = start + grace
    streak = {role: 0 for role in retired}
    first_clear = {}
    count = 0
    while True:
        now = time.monotonic()
        states = {role: h.state() for role, h in retired.items()}
        count += 1
        for role, state in states.items():
            if state == "gone":
                streak[role] += 1
                first_clear.setdefault(role, round(now - start, 5))
            else:
                streak[role] = 0
                first_clear.pop(role, None)
        if now >= deadline:
            break
        await asyncio.sleep(min(0.1, deadline - now))
    row = dict(
        trigger=trigger,
        start=round(start - reg.start, 5),
        elapsed=round(now - start, 5),
        samples=count,
        states=states,
        clear_samples=streak,
        clear_after=first_clear,
    )
    if successor is not None:
        current = {r: h.state() for r, h in reg.roles().items() if r.startswith(f"{successor}/")}
        row["current_at_deadline"] = current
    reg.report["checks"].append(row)
    row["zombie_count"] = sum(v == "zombie" for v in states.values())
    row["active_at_deadline"] = {}
    for role, handle in retired.items():
        if states[role] == "active":
            try:
                item = stat(handle.birth["pid"])
                if item["start_ticks"] == handle.birth["start_ticks"]:
                    row["active_at_deadline"][role] = item
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
    if now - deadline > 0.15:
        reason = "Observer missed its deadline sample"
    elif "active" in states.values() or "zombie" in states.values():
        reason = (
            "Registered identity active at retirement deadline"
            if "active" in states.values()
            else "Unreaped registered fixture identity at retirement deadline"
        )
        row.update(verdict="fail", reason=reason)
        raise Violation(reason)
    elif successor is not None and (len(current) != 2 or set(current.values()) != {"active"}):
        reason = "Expected one active successor pair at the deadline"
    elif any(n < 3 for n in streak.values()):
        reason = "Insufficient consecutive clear samples"
    else:
        row["verdict"] = "pass"
        return
    row.update(verdict="inconclusive", reason=reason)
    raise Uncertain(reason)
