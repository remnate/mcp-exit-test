"""Exercise the installed CLI from a clean private working directory."""

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from mcp_exit_test.observer import Handle, stat


def check_report(path, verdict):
    report = json.loads((path / "result.json").read_text())
    assert report["verdict"] == verdict, report["reason"]
    assert report["cleanup"] == {"ok": True, "remaining": {}}, report["cleanup"]
    cases = ET.parse(path / "junit.xml").findall("testcase")
    assert cases[1].find("error") is None
    tag = {"pass": None, "fail": "failure", "inconclusive": "skipped"}[verdict]
    assert [child.tag for child in cases[0]] == ([tag] if tag else [])
    # Independent postcondition, using the kernel birth record from each event.
    for event in report["timeline"]:
        if event["event"] in ("birth", "driver_birth"):
            b = event["identity"]
            try:
                now = stat(b["pid"])
            except (FileNotFoundError, ProcessLookupError):
                continue
            assert now["start_ticks"] != b["start_ticks"], (event["event"], now)
    return report


def trials(root, out, sdk, sentinel):
    for driver, launcher, reconnects, policy, final in [
        ("codex", "direct", 3, "clean", "eof"),
        ("codex", "npm", 3, "clean", "eof"),
        ("codex", "direct", 0, "clean", "interrupt"),
        ("sdk", "direct", 1, "clean", "eof"),
        ("sdk", "npm", 1, "clean", "eof"),
        ("sdk", "direct", 0, "survive", "eof"),
    ]:
        name = f"{driver}-{launcher}-{policy}-{final}"
        dest = out / name
        cmd = [
            "mcp-exit-test",
            "--strict",
            "--sdk-dir",
            sdk,
            "--driver",
            driver,
            "--launcher",
            launcher,
            "--policy",
            policy,
            "--exit",
            final,
            "--reconnects",
            str(reconnects),
            "--out",
            dest,
        ]
        run = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=95)
        control = policy == "survive"
        assert run.returncode == int(control), run.stdout + run.stderr
        report = check_report(dest, "fail" if control else "pass")
        births = [e for e in report["timeline"] if e["event"] == "birth"]
        assert len(births) == 2 * (1 + reconnects)
        assert len({e["instance"] for e in births}) == 1 + reconnects
        assert {e["generation"] for e in births} == set(range(1, reconnects + 2))
        assert all(e["ancestry"] for e in births if e["role"] == "server" and launcher == "npm")
        assert sentinel.state() == "active"
        for birth in (e for e in births if e["role"] == "server"):
            steps = [e for e in report["timeline"] if e.get("instance") == birth["instance"]]
            response, initialized = (
                e for e in steps if e["event"] in ("initialize_response", "initialized")
            )
            assert response["protocol"] == initialized["protocol"] in ("2025-06-18", "2025-11-25")
            assert (
                response["t"]
                <= initialized["t"]
                <= next(e["t"] for e in steps if e["event"] == "who_called")
            )
        if control:
            check = report["checks"][-1]
            (survivor,) = check["active_at_deadline"].values()
            server, child = [e["identity"] for e in births]
            assert survivor["pid"] == child["pid"]
            assert survivor["ppid"] != server["pid"] and child["ppid"] == server["pid"]
            assert child["sid"] != server["sid"] and child["pgid"] != server["pgid"]
        else:
            assert any(e["event"] == "driver_exit" for e in report["timeline"])
            assert all(set(c["states"].values()) == {"gone"} for c in report["checks"])
        print(
            f"{name}: expected {report['verdict']}, cleanup verified, sentinel active", flush=True
        )

    dest = out / "interrupted-observer"
    with (root / "progress").open("w") as log:
        with subprocess.Popen(
            ["mcp-exit-test", "--driver", "codex", "--strict", "--out", dest], cwd=root, stdout=log
        ) as proc:
            until = time.monotonic() + 15
            while "Ready generation" not in (root / "progress").read_text():
                if proc.poll() is not None or time.monotonic() > until:
                    proc.terminate()
                    raise AssertionError("Interrupted-observer baseline unavailable")
                time.sleep(0.02)
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(timeout=7) == 2
    report = check_report(dest, "inconclusive")
    assert report["reason"] == "Observer interrupted"
    assert sentinel.state() == "active"
    print("Interrupted observer cleaned registered identities; sentinel active", flush=True)

    # Inject stalls into a disposable SDK driver after a real fixture handshake.
    for method, statement in (
        ("connect", "await client.connect("),
        ("close", "await client.close();"),
    ):
        stalled = root / f"hung-{method}"
        stalled.mkdir()
        (stalled / "node_modules").symlink_to(sdk / "node_modules", target_is_directory=True)
        source = (sdk / "driver.mjs").read_text()
        if method == "connect":
            pos = source.index("\n", source.index(statement))
        else:
            pos = source.index(statement)
        source = source[:pos] + "\nawait new Promise(() => {});\n" + source[pos:]
        (stalled / "driver.mjs").write_text(source)
        dest = out / stalled.name
        run = subprocess.run(
            [
                "mcp-exit-test",
                "--driver",
                "sdk",
                "--sdk-dir",
                stalled,
                "--strict",
                "--reconnects",
                "0",
                "--out",
                dest,
            ],
            cwd=root,
            capture_output=True,
            timeout=17,
        )
        assert run.returncode == 2, run.stdout
        report = check_report(dest, "inconclusive")
        assert report["reason"] == "Operation or scenario deadline expired"
        assert any(e["event"] == "initialized" for e in report["timeline"])
        assert sentinel.state() == "active"
        print(f"Hung {method} expired; cleanup verified, sentinel active", flush=True)


def evidence_controls(root, out, sdk, sentinel):
    controls = Path(__file__).resolve().parent
    for name, driver, strict, policy, code in (
        ("missing-initialize", "missing-initialize", True, "clean", 2),
        ("missing-notification", "missing-notification", True, "clean", 2),
        ("empty-client-info", "empty-client-info", True, "clean", 2),
        ("scalar-notification", "scalar-notification", True, "clean", 2),
        ("response-in-request", "response-in-request", True, "clean", 2),
        ("id-in-notification", "id-in-notification", True, "clean", 2),
        ("extended-handshake", "extended-handshake", True, "clean", 0),
        ("late-invalid-notification", "late-invalid-notification", True, "clean", 2),
        ("unbound-uuid", "unbound-uuid", True, "clean", 2),
        ("verified-stale", "verified-stale", True, "clean", 1),
        ("survivor-timeout-strict", "stalled-successor", True, "survive", 1),
        ("survivor-timeout-default", "stalled-successor", False, "survive", 1),
        ("legacy-poll", "legacy-poll", True, "clean", 0),
    ):
        variant = root / driver
        env = os.environ.copy()
        if driver == "legacy-poll":
            variant = sdk
            hooks = root / "legacy-poll-hooks"
            hooks.mkdir()
            shutil.copyfile(controls / "mask_pidfd_hup.py", hooks / "sitecustomize.py")
            env.update(PYTHONPATH=str(hooks), MCP_EXIT_TEST_POLL_RECEIPT=str(root / "poll-seen"))
        elif not variant.exists():
            variant.mkdir()
            (variant / "node_modules").symlink_to(sdk / "node_modules", target_is_directory=True)
            shutil.copyfile(controls / "control_driver.mjs", variant / "driver.mjs")
        dest = out / name
        cmd = [
            "mcp-exit-test",
            "--driver",
            "sdk",
            "--sdk-dir",
            variant,
            "--reconnects",
            "1",
            "--grace",
            "0.6",
            "--policy",
            policy,
            "--out",
            dest,
        ]
        if strict:
            cmd.append("--strict")
        run = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=20)
        assert run.returncode == code, (name, run.stdout, run.stderr)
        report = check_report(dest, {0: "pass", 1: "fail", 2: "inconclusive"}[code])
        events = report["timeline"]
        kinds = [e["event"] for e in events]
        if driver.startswith("missing-"):
            assert "birth" in kinds and "initialized" not in kinds and "who" not in kinds
            assert ("initialize_response" in kinds) == (driver == "missing-notification")
            assert ("handshake_rejected" in kinds) == (driver == "missing-initialize")
        elif driver in (
            "empty-client-info",
            "scalar-notification",
            "response-in-request",
            "id-in-notification",
        ):
            bad_request = driver in ("empty-client-info", "response-in-request")
            method = "initialize" if bad_request else "notifications/initialized"
            assert any(e["event"] == "handshake_rejected" and e["method"] == method for e in events)
            assert ("initialize_response" in kinds) == (not bad_request)
            assert "birth" in kinds and {"initialized", "who_called", "who"}.isdisjoint(kinds)
            assert report["reason"] == "Fixture rejected the MCP handshake"
        elif driver == "extended-handshake":
            assert [e["protocol"] for e in events if e["event"] == "initialized"] == [
                "2025-06-18",
                "2025-11-25",
            ]
            assert [e["generation"] for e in events if e["event"] == "who"] == [1, 2, 2]
            assert "handshake_rejected" not in kinds
        elif driver == "late-invalid-notification":
            assert report["reason"] == "Fixture rejected the MCP handshake"
            assert kinds.index("handshake_rejected") > max(
                i for i, k in enumerate(kinds) if k == "who"
            )
            assert all(c["verdict"] == "pass" for c in report["checks"])
        elif driver == "unbound-uuid":
            assert len({e["instance"] for e in events if e["event"] == "initialized"}) == 2
            assert [e["generation"] for e in events if e["event"] == "who_called"] == [1, 2]
            assert (
                report["reason"] == "Tool reply could not be bound to an initialized fixture call"
            )
        elif driver == "verified-stale":
            assert report["reason"] == "Two calls returned the same unexpected verified fixture"
            assert [e["generation"] for e in events if e["event"] == "who_called"] == [1, 1, 1]
        elif driver == "stalled-successor":
            (check,) = report["checks"]
            assert check["verdict"] == "fail" and set(check["states"].values()) == {
                "gone",
                "active",
            }
            assert next(iter(check["active_at_deadline"])).endswith("/child")
            (issue,) = (e for e in events if e["event"] == "successor_issue")
            assert issue["cause"] == "TimeoutError" and issue["generation"] == 2
            assert report["additional_outcome"]["verdict"] == "inconclusive"
        else:
            assert (root / "poll-seen").is_file()
            assert all(set(c["states"].values()) == {"gone"} for c in report["checks"])
        assert sentinel.state() == "active"
        print(f"{name}: expected exit {code}, cleanup verified, sentinel active", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="exit-test-release-") as tmp:
        root = Path(tmp)
        out = args.out.resolve() if args.out else root / "reports"
        out.mkdir(parents=True)
        with subprocess.Popen(["sleep", "300"], start_new_session=True) as other:
            handle = Handle(other.pid)
            try:
                sdk = args.sdk_dir.resolve()
                trials(root, out, sdk, handle)
                evidence_controls(root, out, sdk, handle)
                env = {"PATH": os.environ["PATH"], "HOME": str(root), "CODEX_HOME": str(root)}
                versions = {
                    tool: subprocess.check_output(
                        [tool, "--version"],
                        cwd=root,
                        env=env,
                        text=True,
                        timeout=5,
                        stderr=subprocess.DEVNULL,
                    ).strip()
                    for tool in ("node", "npm", "codex")
                }
                versions["sdk"] = json.loads(
                    (sdk / "node_modules/@modelcontextprotocol/sdk/package.json").read_text()
                )["version"]
                versions.update(python=platform.python_version(), linux=platform.release())
                receipt = dict(
                    versions=versions,
                    sentinel_birth=handle.birth,
                    sentinel_after_all_checks=handle.state(),
                    scenarios=sorted(path.parent.name for path in out.glob("*/result.json")),
                    registered_post_cleanup_identities_remaining=0,
                )
                (out / "release.json").write_text(json.dumps(receipt, indent=2) + "\n")
            finally:
                handle.close()
                other.terminate()


if __name__ == "__main__":
    main()
