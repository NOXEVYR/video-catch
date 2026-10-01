"""Failure and cancelled-exit recovery without recording user devices."""
import sys
from pathlib import Path
import threading
import queue
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


class LifecycleTests(unittest.TestCase):
    def test_capture_rejects_relative_folder_before_filesystem_changes(self):
        app = App.__new__(App)
        app.folder = Mock(get=Mock(return_value="relative-capture"))
        with patch("app.Path.mkdir") as mkdir:
            for options in ({}, {"folder": "relative-capture"}, {"folder": "C:relative"}):
                with self.subTest(options=options), self.assertRaisesRegex(ValueError, "绝对路径"):
                    app._capture_folder(options)
            mkdir.assert_not_called()

    def test_polling_recovers_after_unexpected_callback_error(self):
        app = App.__new__(App)
        app.closing = False
        app.root = Mock()
        app.notice = Mock()
        app._tick_once = Mock(side_effect=[RuntimeError("fixture failure"), None])
        app.tick()
        app.root.after.assert_called_once_with(500, app.tick)
        app.notice.set.assert_called_once()
        app.tick()
        self.assertEqual(app.root.after.call_count, 2)

    def test_cancel_exit_after_recording_resumes_ai_and_polling(self):
        app = App.__new__(App)
        app.storage_settings = {}
        app.closing = False
        app.root = Mock()
        app.notice = Mock()
        app.ai = Mock(enabled=threading.Event())
        app.ai_enabled = Mock(get=Mock(return_value=True))
        app.recorder = Mock(is_busy=True)
        app.screenshot_pending = False
        app.exit_after_capture = False
        app.jobs = {}
        app.pending = [("fixture", "folder")]
        app.ai.enabled.set()
        with patch("app.messagebox.askyesno", side_effect=[True, False]):
            app.close()
            self.assertTrue(app.exit_after_capture)
            self.assertFalse(app.ai.enabled.is_set())
            app.recorder.is_busy = False
            app._refresh_capture = Mock()
            app.tick()
        self.assertFalse(app.closing)
        self.assertFalse(app.exit_after_capture)
        self.assertTrue(app.ai.enabled.is_set())
        app.root.after.assert_called_once_with(500, app.tick)

    def test_empty_folder_is_rejected_without_opening_cwd(self):
        app = App.__new__(App)
        app.root = Mock()
        app.folder = Mock(get=Mock(return_value="  "))
        with patch("app.open_local") as open_local, patch("app.messagebox.showerror") as error:
            app.open_folder()
        open_local.assert_not_called()
        error.assert_called_once()

    def test_capture_failure_aborts_automatic_exit_and_remains_visible(self):
        app = App.__new__(App)
        app.root = Mock()
        app.notice = Mock()
        app.store = Mock()
        app.ai = Mock(enabled=threading.Event())
        app.ai_enabled = Mock(get=Mock(return_value=True))
        app.exit_after_capture = True
        app.capture_events = queue.Queue()
        app.recorder = Mock(snapshot=Mock(return_value={"id": "fixture", "status": "失败", "error": "Disk full"}))
        app.recording_notice_id = None
        app.recording_panel = app.recording_toolbar = None
        app._refresh_capture()
        self.assertFalse(app.exit_after_capture)
        self.assertTrue(app.ai.enabled.is_set())
        app.root.deiconify.assert_called_once()
        self.assertIn("Disk full", app.notice.set.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
