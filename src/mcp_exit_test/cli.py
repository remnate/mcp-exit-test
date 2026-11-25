"""Command line and portable evidence files."""

import argparse
import asyncio
import json
import platform
import signal
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .observer import Uncertain, Violation
from .scenario import Scenario


async def measure(args):
    report = dict(
        schema=1,
        version="0.1.0",
        scope="registered-fixture-pairs",
        driver=args.driver,
        launcher=args.launcher,
        policy=args.policy,
        exit=args.exit,
        reconnects=args.reconnects,
        grace_seconds=args.grace,
        linux=platform.release(),
        python=platform.python_version(),
        coverage=dict(
            server="must retire",
            child="must retire",
            driver="must exit on final close",
            launcher="ancestry only",
            other_descendants="outside scope",
        ),
        timeline=[],
        checks=[],
    )
    with tempfile.TemporaryDirectory(prefix="mcp-exit-") as scratch:
        s = Scenario(args, Path(scratch), report)
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, task.cancel)
        try:
            async with asyncio.timeout(85):
                await s.reg.open()
                await s.run()
            verdict, reason = "pass", "Registered fixture pairs met their retirement deadlines"
        except Violation as exc:
            verdict, reason = "fail", str(exc)
        except Uncertain as exc:
            verdict, reason = "inconclusive", str(exc)
        except TimeoutError:
            verdict, reason = "inconclusive", "Operation or scenario deadline expired"
        except asyncio.CancelledError:
            verdict, reason = "inconclusive", "Observer interrupted"
        except (OSError, KeyError, ValueError, TypeError, IndexError) as exc:
            verdict, reason = (
                "inconclusive",
                f"Missing capability or evidence ({type(exc).__name__})",
            )
        except Exception as exc:
            verdict, reason = "error", f"Observer error ({type(exc).__name__})"
        if verdict == "inconclusive":
            for check in report["checks"]:
                if check.get("verdict") == "fail":
                    report["additional_outcome"] = dict(verdict=verdict, reason=reason)
                    verdict, reason = "fail", check["reason"]
                    break
        report.update(verdict=verdict, reason=reason)
        s.reg.log("verdict", verdict=verdict)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: None)
        try:
            async with asyncio.timeout(5):
                remaining = await s.cleanup()
            report["cleanup"] = dict(ok=not remaining, remaining=remaining)
        except Exception as exc:
            report["cleanup"] = dict(ok=False, error=type(exc).__name__, remaining=s.remaining())
        finally:
            for handle in s.handles().values():
                handle.close()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
    return report


def write_results(report, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    suite = ET.Element("testsuite", name="mcp-exit-test", tests="2")
    case = ET.SubElement(
        suite,
        "testcase",
        name="registered-fixture-pairs",
        classname=f"{report['driver']}.{report['launcher']}.{report['policy']}",
    )
    tag = {"fail": "failure", "inconclusive": "skipped", "error": "error"}.get(report["verdict"])
    if tag:
        ET.SubElement(case, tag, message=report["reason"])
    teardown = ET.SubElement(suite, "testcase", name="owned-process-cleanup")
    if not report["cleanup"]["ok"]:
        ET.SubElement(teardown, "error", message="Could not verify cleanup")
    for attr, elem in [("failures", "failure"), ("skipped", "skipped"), ("errors", "error")]:
        suite.set(attr, str(len(suite.findall(".//" + elem))))
    ET.ElementTree(suite).write(out / "junit.xml", encoding="utf-8", xml_declaration=True)


def main():
    p = argparse.ArgumentParser(description="Witness registered Linux MCP fixture lifetimes.")
    p.add_argument("--driver", choices=["codex", "sdk"], default="codex")
    p.add_argument(
        "--sdk-dir", type=Path, help="Prepared directory containing driver.mjs and node_modules"
    )
    p.add_argument("--launcher", choices=["direct", "npm"], default="direct")
    p.add_argument(
        "--policy",
        choices=["clean", "survive"],
        default="clean",
        help="Fixture child policy when its server retires",
    )
    p.add_argument("--reconnects", type=int, choices=range(4), default=3)
    p.add_argument(
        "--exit",
        choices=["eof", "interrupt"],
        default="eof",
        help="Close driver input or send SIGTERM to the verified host",
    )
    p.add_argument(
        "--grace", type=float, default=5, help="Fixed observation window, 0.5 to 5 seconds"
    )
    p.add_argument("--out", type=Path, default=Path("exit-results"))
    p.add_argument("--strict", action="store_true", help="Exit 2 for an inconclusive result")
    a = p.parse_args()
    if sys.platform != "linux" or not 0.5 <= a.grace <= 5:
        p.error("Linux and a grace period from 0.5 to 5 seconds are required")
    if a.driver == "sdk":
        if not a.sdk_dir or not (a.sdk_dir / "driver.mjs").is_file():
            p.error("--driver sdk requires a prepared --sdk-dir")
        a.sdk_dir = a.sdk_dir.resolve()
        if a.exit == "interrupt":
            p.error("--exit interrupt requires --driver codex")
    r = asyncio.run(measure(a))
    write_results(r, a.out)
    for event in r["timeline"]:
        if event["event"] == "birth":
            b = event["identity"]
            print(
                f"generation {event['generation']} {event['role']} {event['instance']} "
                f"pid {b['pid']} start {b['start_ticks']}"
            )
    for check in r["checks"]:
        print(f"{check['trigger']} +{check['elapsed']:.3f}s {json.dumps(check['states'])}")
    print(
        f"{r['verdict']} — {r['reason']}. Cleanup {'verified' if r['cleanup']['ok'] else 'failed'}."
    )
    if not r["cleanup"]["ok"] or r["verdict"] == "error":
        return 3
    if r["verdict"] == "inconclusive" and (a.strict or r["reason"] == "Observer interrupted"):
        return 2
    return int(r["verdict"] == "fail")


if __name__ == "__main__":
    sys.exit(main())
