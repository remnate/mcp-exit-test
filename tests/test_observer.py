import asyncio
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mcp_exit_test.cli import write_results
from mcp_exit_test.observer import Handle, Registry, Uncertain, Violation, observe
from mcp_exit_test.scenario import Scenario


class BirthTests(unittest.TestCase):
    def test_pid_reuse_and_unreadable_identity_do_not_signal(self):
        h = Handle(os.getpid())
        self.addCleanup(h.close)
        with patch("signal.pidfd_send_signal") as send:
            with patch("mcp_exit_test.observer.stat", return_value={**h.birth, "start_ticks": 0}):
                self.assertEqual(h.state(), "gone")
                h.send(signal.SIGTERM)
            with patch("mcp_exit_test.observer.stat", side_effect=PermissionError):
                self.assertEqual(h.state(), "unknown")
                h.send(signal.SIGKILL)
            send.assert_not_called()

    def test_hidden_proc_entry_with_live_pidfd(self):
        h = Handle(os.getpid())
        self.addCleanup(h.close)
        with patch("mcp_exit_test.observer.stat", side_effect=FileNotFoundError):
            self.assertEqual(h.state(), "unknown")

    def test_readable_pidfd_distinguishes_zombie_and_reaped_without_hangup(self):
        with subprocess.Popen(["cat"], stdin=subprocess.PIPE) as child:
            h = Handle(child.pid)
            self.addCleanup(h.close)
            child.stdin.close()
            # waitid with WNOWAIT leaves the zombie owned by this test.
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
            h.poller = Mock()
            h.poller.poll.return_value = [(h.fd, select.POLLIN)]
            self.assertEqual(h.state(), "zombie")
            with patch("mcp_exit_test.observer.stat", side_effect=PermissionError):
                self.assertEqual(h.state(), "unknown")
            child.wait(timeout=2)
            self.assertEqual(h.state(), "gone")
            with patch("pathlib.Path.read_bytes", side_effect=PermissionError):
                self.assertEqual(h.state(), "unknown")

    def test_rapid_exit_without_birth_evidence(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait(timeout=2)
        with self.assertRaises((FileNotFoundError, ProcessLookupError)):
            Handle(p.pid)

    def test_rapid_exit_after_registration(self):
        with subprocess.Popen(["cat"], stdin=subprocess.PIPE) as proc:
            h = Handle(proc.pid)
            self.addCleanup(h.close)
            proc.communicate(timeout=2)
            self.assertEqual(h.state(), "gone")


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.reg = Registry("", {"timeline": [], "checks": []})

    async def test_live_parent_holds_zombie(self):
        # This test process is the live, intentionally non-reaping parent.
        proc = subprocess.Popen(["cat"], stdin=subprocess.PIPE)
        try:
            h = Handle(proc.pid)
            self.addCleanup(h.close)
            proc.stdin.close()
            with self.assertRaisesRegex(Violation, "Unreaped"):
                await observe(self.reg, {"child": h}, 0.35, "test close", time.monotonic())
            row = self.reg.report["checks"][0]
            self.assertEqual(row["states"], {"child": "zombie"})
            self.assertEqual(row["zombie_count"], 1)
        finally:
            proc.wait(timeout=2)

    async def test_hung_readiness_and_close(self):
        with tempfile.TemporaryDirectory() as temp:
            s = Scenario(SimpleNamespace(), Path(temp), {"timeline": []})
            s.send = AsyncMock()
            s.proc = SimpleNamespace(stdout=asyncio.StreamReader())
            with patch("mcp_exit_test.scenario.API_TIMEOUT", 0.1):
                for method in ["initialize", "close"]:
                    with self.assertRaises(TimeoutError):
                        await s.rpc(method, {})

    async def test_unknown_is_inconclusive(self):
        h = SimpleNamespace(state=lambda: "unknown")
        with self.assertRaises(Uncertain):
            await observe(self.reg, {"child": h}, 0.3, "test", time.monotonic())


class ReportTests(unittest.TestCase):
    def test_separate_product_and_cleanup_cases(self):
        import xml.etree.ElementTree as ET

        with tempfile.TemporaryDirectory() as temp:
            for verdict, tag in [
                ("fail", "failure"),
                ("error", "error"),
                ("inconclusive", "skipped"),
            ]:
                write_results(
                    dict(
                        driver="sdk",
                        launcher="direct",
                        policy="clean",
                        verdict=verdict,
                        reason="test",
                        cleanup={"ok": False},
                    ),
                    Path(temp),
                )
                cases = ET.parse(Path(temp) / "junit.xml").findall("testcase")
                self.assertIsNotNone(cases[0].find(tag))
                self.assertIsNotNone(cases[1].find("error"))
