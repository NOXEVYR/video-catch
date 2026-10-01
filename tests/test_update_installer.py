"""Synthetic owned-file transaction, exit handoff, restart and rollback."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_updater import Response, metadata, synthetic_archive
from updater import API, UpdateError, Updater, verify_package
from update_installer import (confirm_started, install_prepared, launch_helper, main, prepare_update, process_creation_time,
                              recover_pending, wait_for_exact_exit,
                              _journal_path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class FakeProcess:
    pid = 99999
    def __init__(self, running=False):
        self.running = running
    def poll(self):
        return None if self.running else 1


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / "installed"
        self.app.mkdir()
        self.state = self.base / "state"
        old = {"VideoCatch.exe": b"old desktop", "VideoCatchAI.exe": b"old client",
               "version.py": b'VERSION = "0.5.0"\n', "obsolete.txt": b"old registered"}
        for name, data in old.items():
            (self.app / name).write_bytes(data)
        old_manifest = {"version": "0.5.0", "files": {name: {"bytes": len(data), "sha256": digest(data)}
                                                   for name, data in old.items()}}
        (self.app / "runtime-manifest.json").write_text(json.dumps(old_manifest), encoding="utf-8")
        (self.app / "my-clips.txt").write_text("personal data", encoding="utf-8")
        archive = self.base / "update.zip"
        release = synthetic_archive(archive, extra={"new.txt": b"new registered"})
        self.package = verify_package(archive, release)

    def plan(self):
        return prepare_update(self.package, self.app, self.state)

    def test_prepare_reuses_only_registered_unchanged_and_refuses_local_edit(self):
        plan = self.plan()
        self.assertIn("obsolete.txt", {op.name for op in plan.operations})
        self.assertNotIn("my-clips.txt", {op.name for op in plan.operations})
        (self.app / "VideoCatch.exe").write_bytes(b"user modified executable")
        with self.assertRaisesRegex(UpdateError, "被修改"):
            self.plan()

    def test_prepare_refuses_unregistered_target_collision(self):
        (self.app / "new.txt").write_text("user file", encoding="utf-8")
        with self.assertRaisesRegex(UpdateError, "未登记"):
            self.plan()

    def test_waits_for_exact_host_then_replaces_and_confirms_restart(self):
        plan = self.plan()
        calls = []
        def wait(pid, created, timeout):
            calls.append((pid, created, timeout))
            return True
        with patch("update_installer.process_creation_time", return_value=777):
            result = install_prepared(plan, host_pid=123, host_created=456, wait_fn=wait,
                launch_fn=lambda *_: FakeProcess(running=True), verify_fn=lambda *_: True)
        self.assertEqual(result["status"], "installed")
        self.assertEqual(calls, [(123, 456, 120)])
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"new desktop")
        self.assertFalse((self.app / "obsolete.txt").exists())
        self.assertEqual((self.app / "my-clips.txt").read_text(encoding="utf-8"), "personal data")
        self.assertEqual(json.loads((self.app / "runtime-manifest.json").read_text())["version"], "0.6.0")
        self.assertEqual(recover_pending(self.state, self.app)["status"], "committed")

    def test_restart_failure_rolls_back_program_and_keeps_user_file(self):
        plan = self.plan()
        with self.assertRaisesRegex(UpdateError, "已回滚"):
            install_prepared(plan, host_pid=123, host_created=456, wait_fn=lambda *_: True,
                launch_fn=lambda *_: FakeProcess(running=False), verify_fn=lambda *_: False)
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"old desktop")
        self.assertEqual((self.app / "VideoCatchAI.exe").read_bytes(), b"old client")
        self.assertTrue((self.app / "obsolete.txt").is_file())
        self.assertFalse((self.app / "new.txt").exists())
        self.assertEqual((self.app / "my-clips.txt").read_text(encoding="utf-8"), "personal data")
        self.assertEqual(json.loads((self.app / "runtime-manifest.json").read_text())["version"], "0.5.0")
        self.assertEqual(recover_pending(self.state, self.app)["status"], "rolled_back")

    def test_host_timeout_never_changes_program(self):
        plan = self.plan()
        with self.assertRaisesRegex(UpdateError, "仍在运行"):
            install_prepared(plan, host_pid=123, host_created=456, wait_fn=lambda *_: False)
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"old desktop")
        self.assertFalse(_journal_path(self.state).exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows process identity")
    def test_windows_wait_tracks_one_pid_and_creation_time_without_termination(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            created = process_creation_time(process.pid)
            self.assertFalse(wait_for_exact_exit(process.pid, created, .01))
            self.assertTrue(wait_for_exact_exit(process.pid, created, 3))
            self.assertEqual(process.wait(timeout=1), 0)
        finally:
            if process.poll() is None:
                process.wait(timeout=3)

    def test_running_unconfirmed_app_is_not_rolled_back_under_it(self):
        plan = self.plan()
        with patch("update_installer.process_creation_time", return_value=777):
            result = install_prepared(plan, host_pid=123, host_created=456, wait_fn=lambda *_: True,
                launch_fn=lambda *_: FakeProcess(running=True), verify_fn=lambda *_: None)
        self.assertEqual(result["status"], "restart_unconfirmed")
        self.assertEqual(recover_pending(self.state, self.app, child_exited=lambda *_: False)["status"], "awaiting_running_app")
        self.assertEqual(recover_pending(self.state, self.app, child_exited=lambda *_: True)["status"], "rolled_back")
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"old desktop")

    def test_partial_backup_crash_has_no_program_rollback_work(self):
        plan = self.plan()
        journal = {"id": "synthetic", "status": "backing_up", "operations": [], "child_pid": None}
        path = _journal_path(self.state)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(journal), encoding="utf-8")
        self.assertEqual(recover_pending(self.state, self.app)["status"], "rolled_back")
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"old desktop")

    def test_crash_after_launch_before_child_identity_never_rolls_back_running_app(self):
        path = _journal_path(self.state)
        path.parent.mkdir(parents=True)
        journal = {"id": "crash-window", "status": "restarting", "target_version": "0.6.0",
                   "operations": [], "child_pid": None, "child_created": None}
        path.write_text(json.dumps(journal), encoding="utf-8")
        self.assertEqual(recover_pending(self.state, self.app)["status"], "unknown_child_identity")
        self.assertEqual(json.loads(path.read_text())["status"], "restarting")
        marker = self.state / "updates" / "confirmed-crash-window.json"
        marker.write_text(json.dumps({"id": "crash-window", "version": "0.6.0", "pid": 99999}), encoding="utf-8")
        self.assertEqual(recover_pending(self.state, self.app)["status"], "committed")

    def test_late_ready_confirmation_commits_pending_transaction(self):
        plan = self.plan()
        with patch("update_installer.process_creation_time", return_value=777):
            result = install_prepared(plan, host_pid=123, host_created=456, wait_fn=lambda *_: True,
                launch_fn=lambda *_: FakeProcess(running=True), verify_fn=lambda *_: None)
        with patch("update_installer.os.getpid", return_value=99999):
            confirm_started(self.state, result["transaction"], "0.6.0")
        self.assertEqual(recover_pending(self.state, self.app)["status"], "committed")
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"new desktop")

    def test_helper_runs_from_private_copy_outside_install_tree(self):
        helper = self.app / "VideoCatchUpdater.exe"
        helper.write_bytes(b"synthetic helper")
        handoff = self.state / "updates" / "handoff.json"
        handoff.parent.mkdir(parents=True)
        handoff.write_text("{}", encoding="utf-8")
        with patch("update_installer.subprocess.Popen", return_value=FakeProcess()) as popen:
            launch_helper(helper, handoff, self.state)
        copied = Path(popen.call_args.args[0][0])
        self.assertNotEqual(copied, helper)
        self.assertEqual(copied.read_bytes(), helper.read_bytes())
        self.assertEqual(popen.call_args.args[0][1:], ["--handoff", str(handoff)])

    def test_helper_records_preflight_error_without_touching_install(self):
        handoff = self.state / "updates" / "handoff.json"
        handoff.parent.mkdir(parents=True)
        handoff.write_text(json.dumps({"state_dir": str(self.state), "release": {},
                                       "package": str(self.package.path), "install_root": str(self.app),
                                       "host_pid": 1, "host_created": 1}), encoding="utf-8")
        self.assertEqual(main(["--handoff", str(handoff)]), 2)
        report = json.loads((self.state / "updates" / "last-result.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "error")
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"old desktop")

    def test_full_synthetic_check_download_exit_replace_restart(self):
        archive = self.package.path.read_bytes()
        def opener(request, timeout):
            return Response(json.dumps(metadata(self.package.release)).encode() if request.full_url == API else archive)
        updater = Updater(self.state, "0.5.0", self.app, opener=opener)
        check = updater.check()
        self.assertEqual(check["status"], "available")
        staged = updater.stage(check["release"])
        plan = updater.prepare(staged)
        with patch("update_installer.process_creation_time", return_value=777):
            result = install_prepared(plan, host_pid=123, host_created=456, wait_fn=lambda *_: True,
                launch_fn=lambda *_: FakeProcess(running=True), verify_fn=lambda *_: True)
        self.assertEqual(result["status"], "installed")
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"new desktop")
        self.assertEqual((self.app / "my-clips.txt").read_text(encoding="utf-8"), "personal data")

    def test_full_synthetic_check_download_exit_restart_failure_rolls_back(self):
        archive = self.package.path.read_bytes()
        def opener(request, timeout):
            return Response(json.dumps(metadata(self.package.release)).encode() if request.full_url == API else archive)
        updater = Updater(self.state, "0.5.0", self.app, opener=opener)
        release = updater.check()["release"]
        plan = updater.prepare(updater.stage(release))
        with self.assertRaisesRegex(UpdateError, "已回滚"):
            install_prepared(plan, host_pid=123, host_created=456, wait_fn=lambda *_: True,
                launch_fn=lambda *_: FakeProcess(running=False), verify_fn=lambda *_: False)
        self.assertEqual((self.app / "VideoCatch.exe").read_bytes(), b"old desktop")
        self.assertEqual((self.app / "my-clips.txt").read_text(encoding="utf-8"), "personal data")


if __name__ == "__main__":
    unittest.main()
