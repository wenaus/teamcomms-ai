"""Focused shutdown regressions; no database, native clients, or network."""

import asyncio
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from teamcomms.connectors import launcher


class ShutdownChecks(unittest.TestCase):
    def test_owned_group_escalation_includes_helpers(self):
        # Both the group leader and its helper ignore TERM. An unrelated
        # process must survive the wrapper's group-scoped escalation.
        helper = "import time; print('helper ready', flush=True); time.sleep(60)"
        code = ("import signal,subprocess,sys,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"subprocess.Popen([sys.executable, '-c', {helper!r}]); "
                "time.sleep(60)")
        owned = subprocess.Popen([sys.executable, "-c", code], start_new_session=True,
                                 stdout=subprocess.PIPE)
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                     start_new_session=True)
        try:
            self.assertTrue(select.select([owned.stdout], [], [], 3)[0], "fixture startup stalled")
            self.assertEqual(owned.stdout.readline(), b"helper ready\n")
            with patch.object(launcher, "STOP_GRACE", .02):
                asyncio.run(launcher.stop_process_groups([owned.pid], [owned]))
            self.assertEqual(owned.returncode, -signal.SIGKILL)
            self.assertTrue(select.select([owned.stdout], [], [], 2)[0], "helper still holds pipe")
            self.assertEqual(owned.stdout.read(), b"")
            self.assertIsNone(unrelated.poll())
        finally:
            for process in (owned, unrelated):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                process.wait(timeout=2)
            owned.stdout.close()

    def run_supervisor(self, status, *, unavailable=False, connected=False, cancel=False,
                       disconnect_during_snapshot=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            runtime = {"server_pid": 12345, "launcher_pid": os.getpid(), "cwd": temporary}
            (directory / "runtime.json").write_text(json.dumps(runtime))
            if not connected:
                (directory / "disconnected").touch()
            calls = []

            class Client:
                def __init__(self, *args):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def loaded_threads(self):
                    if unavailable:
                        await asyncio.Event().wait()  # RPC that never returns.
                    return ["native"]

                async def call(self, method, params):
                    calls.append(method)
                    if disconnect_during_snapshot:
                        (directory / "disconnected").touch()
                    return {"thread": {"threadSource": "user", "status": status}}

            async def check():
                task = asyncio.create_task(launcher.supervise(directory, SimpleNamespace(host="fixture"), "config"))
                if cancel:
                    await asyncio.sleep(.005)
                    task.cancel()
                await asyncio.wait_for(task, .5)

            with patch.object(launcher, "CodexClient", Client), \
                 patch.object(launcher.os, "kill"), \
                 patch.object(launcher, "spawn") as spawn, \
                 patch.object(launcher, "stop_process_groups", new_callable=AsyncMock) as stop, \
                 patch.object(launcher, "DISCONNECT_GRACE", .03), \
                 patch.object(launcher, "POLL_INTERVAL", .005):
                spawn.return_value = SimpleNamespace(pid=67890, poll=lambda: None)
                asyncio.run(check())
                self.assertIn(12345, stop.call_args.args[0])
                if not connected:
                    spawn.assert_not_called()
                if disconnect_during_snapshot:
                    self.assertEqual(spawn.call_count, 1)
                    self.assertEqual(stop.call_args_list[0].args[0], [67890])
            self.assertTrue(all(method == "thread/read" for method in calls))
            return calls

    def test_idle_and_unanswerable_requests_stop_without_waiting(self):
        for status in ({"type": "idle"},
                       {"type": "active", "activeFlags": ["waitingOnApproval"]},
                       {"type": "active", "activeFlags": ["waitingOnUserInput"]}):
            with self.subTest(status=status):
                self.assertEqual(len(self.run_supervisor(status)), 1)

    def test_active_work_has_bounded_grace(self):
        self.assertGreater(len(self.run_supervisor({"type": "active"})), 1)

    def test_unresponsive_rpc_cannot_hold_shutdown_open(self):
        self.run_supervisor({}, unavailable=True)

    def test_explicit_supervisor_stop_cleans_up(self):
        self.run_supervisor({}, unavailable=True, connected=True, cancel=True)

    def test_disconnect_stops_receiver_and_does_not_restart_it(self):
        self.run_supervisor({"type": "active", "activeFlags": ["waitingOnApproval"]},
                            connected=True, disconnect_during_snapshot=True)


if __name__ == "__main__":
    unittest.main()
