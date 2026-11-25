"""Loaded as sitecustomize only in the disposable legacy-poll CLI control."""

import os
import select
from pathlib import Path

from mcp_exit_test.observer import Handle

original = Handle.__init__


class LegacyPoll:
    def __init__(self, poller):
        self.poller = poller

    def poll(self, timeout):
        events = self.poller.poll(timeout)
        if any(flags & select.POLLIN for _, flags in events):
            Path(os.environ["MCP_EXIT_TEST_POLL_RECEIPT"]).touch()
        return [(fd, flags & ~select.POLLHUP) for fd, flags in events]


def instrument(handle, pid):
    original(handle, pid)
    handle.poller = LegacyPoll(handle.poller)


Handle.__init__ = instrument
