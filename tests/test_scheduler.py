import fcntl
import json
import plistlib
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from station import scheduler


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = (Path(self.temp.name) / "project with spaces").resolve()
        self.root.mkdir()
        self.data = patch.object(scheduler, "ROOT", Path("data"))
        self.data.start()

    def tearDown(self):
        self.data.stop()
        self.temp.cleanup()

    def test_beijing_next_run_and_calendar_plist(self):
        now = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(scheduler.next_run(now), "2026-10-10T08:00:00+08:00")
        self.assertEqual(scheduler.next_run(now - timedelta(seconds=1)), "2026-10-09T08:00:00+08:00")
        self.assertTrue(scheduler.timezone_matches_beijing(now.astimezone(ZoneInfo("Asia/Shanghai"))))
        self.assertFalse(scheduler.timezone_matches_beijing(now))
        config = plistlib.loads(plistlib.dumps(scheduler.build_plist(self.root, self.root)))
        self.assertEqual(config["StartCalendarInterval"], {"Hour": 8, "Minute": 0})
        self.assertFalse(config["RunAtLoad"])
        self.assertEqual(config["EnvironmentVariables"]["TZ"], "Asia/Shanghai")
        self.assertEqual(config["WorkingDirectory"], str(self.root))
        self.assertEqual(config["ProgramArguments"], [str(self.root / ".venv/bin/python"), "-m", "station.scheduler", "run"])
        self.assertTrue(Path(config["StandardOutPath"]).is_absolute())

    def test_success_and_optional_draft_use_absolute_venv_and_cwd(self):
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "Ingest complete: 2 new records\n")
        result = scheduler.run_once(self.root, runner=runner, auto_draft=True)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([c[0][-1] for c in calls], ["ingest", "prepare"])
        self.assertEqual(calls[0][1]["cwd"], str(self.root))
        self.assertEqual(calls[0][1]["env"]["TZ"], "Asia/Shanghai")
        state = scheduler.get_status(self.root)
        self.assertFalse(state["running"])
        self.assertFalse(state["installed"])
        self.assertFalse(state["catch_up_guaranteed"])
        self.assertEqual(state["time"], "08:00")
        self.assertEqual(state["timezone"], "Asia/Shanghai")
        self.assertEqual(scheduler.status(self.root)["last_run"], state["last_run"])
        self.assertIsNotNone(state["last_run"]["started_at"])
        self.assertIsNotNone(state["last_run"]["finished_at"])
        self.assertEqual(state["last_run"]["status"], "succeeded")

    def test_nonzero_and_caught_feed_failures_are_recorded(self):
        for code, output, reason in [(3, "failure", "command-failed"),
                                     (0, "[ERROR] Feed retrieval failed: TimeoutError\n", "reported-errors")]:
            calls = []
            def runner(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, code, output)
            result = scheduler.run_once(self.root, runner=runner, auto_draft=True)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], reason)
            self.assertEqual(len(calls), 1, "do not draft after collection failure")
            self.assertEqual(scheduler.get_status(self.root)["last_run"]["status"], "failed")

    def test_model_failure_and_exception_do_not_expose_private_error(self):
        def warn(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "[WARN] Could not compile ai: ValueError\n" if args[-1] == "prepare" else "ok\n")
        result = scheduler.run_once(self.root, runner=warn, auto_draft=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["steps"][-1]["action"], "prepare")
        def broken(args, **kwargs):
            raise RuntimeError("secret-api-key-should-never-be-in-status")
        result = scheduler.run_once(self.root, runner=broken)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("secret-api-key", json.dumps(scheduler.get_status(self.root)))

    def test_concurrent_lock_and_crash_release(self):
        p = scheduler.paths(self.root)
        p["logs"].mkdir(parents=True)
        p["state"].write_text('{"status":"running","pid":123}', encoding="utf-8")
        # A separate OS process holds the same advisory lock; no collection runs.
        code = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('LOCKED',flush=True); sys.stdin.read()"
        child = subprocess.Popen([__import__('sys').executable, "-c", code, str(p["lock"])],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "LOCKED")
            self.assertTrue(scheduler.get_status(self.root)["running"])
            def prohibited(*args, **kwargs):
                self.fail("a competing run must not launch a subprocess")
            result = scheduler.run_once(self.root, runner=prohibited)
            self.assertEqual(result, {"status": "skipped", "reason": "already-running"})
            self.assertEqual(json.loads(p["state"].read_text())["pid"], 123)
        finally:
            child.terminate()
            child.communicate(timeout=5)
        self.assertFalse(scheduler.get_status(self.root)["running"])
        self.assertEqual(scheduler.get_status(self.root)["last_run"]["status"], "interrupted")
        success = lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "ok")
        self.assertEqual(scheduler.run_once(self.root, runner=success)["status"], "succeeded")

    def test_install_registers_only_without_running_job(self):
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        calls = []
        def launchctl(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        with patch.object(scheduler.sys, "platform", "darwin"), patch.object(scheduler, "timezone_matches_beijing", return_value=True), patch.object(scheduler.subprocess, "run", side_effect=launchctl):
            result = scheduler.install(self.root, self.root)
        self.assertTrue(result["installed"])
        self.assertTrue(result["loaded"])
        self.assertEqual([c[1] for c in calls], ["bootout", "enable", "bootstrap", "print"])
        self.assertNotIn("kickstart", str(calls))
        self.assertEqual(result["last_run"], {})
        with patch.object(scheduler.sys, "platform", "darwin"), patch.object(scheduler, "timezone_matches_beijing", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "system timezone"):
                scheduler.install(self.root, self.root)


if __name__ == "__main__":
    unittest.main()
