"""Tk-thread tray integration, with no Tk window or real notification icon."""
from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


def fake_app(active=True):
    app = App.__new__(App)
    app.root = Mock()
    app.notice = Mock()
    app.tray = Mock(active=active, events=queue.Queue())
    return app


class TrayAppTests(unittest.TestCase):
    def test_close_button_hides_only_with_active_tray(self):
        app = fake_app(active=True)
        app.close = Mock()
        app.request_close()
        app.root.withdraw.assert_called_once()
        app.close.assert_not_called()
        app.tray.active = False
        app.request_close()
        app.close.assert_called_once()
        app.root.withdraw.assert_called_once()

    def test_events_are_consumed_on_poll_and_restore_or_open_library(self):
        app = fake_app()
        app.open_library = Mock()
        app.close = Mock()
        for name in ("show", "library", "unavailable"):
            app.tray.events.put(name)
        app.root.deiconify.assert_not_called()
        app.poll_tray()
        self.assertEqual(app.root.deiconify.call_count, 2)
        self.assertEqual(app.root.lift.call_count, 2)
        app.open_library.assert_called_once()
        app.close.assert_not_called()

    def test_exit_from_hidden_window_shows_it_before_closing(self):
        app = fake_app()
        order = []
        app.root.deiconify.side_effect = lambda: order.append("show")
        app.close = Mock(side_effect=lambda: order.append("close"))
        app.tray.events.put("exit")
        app.tray.events.put("library")
        app.poll_tray()
        self.assertEqual(order, ["show", "close"])
        self.assertEqual(app.tray.events.get_nowait(), "library")

    def test_tray_exit_while_recording_uses_existing_stop_and_save_flow(self):
        app = fake_app()
        app.recorder = Mock(is_busy=True)
        app.screenshot_pending = False
        app.exit_after_capture = False
        app.ai = Mock(enabled=threading.Event())
        app.ai.enabled.set()
        app.closing = False
        app.tray.events.put("exit")
        with patch("app.messagebox.askyesno", return_value=True) as ask:
            app.poll_tray()
        ask.assert_called_once()
        app.root.deiconify.assert_called_once()
        app.recorder.stop.assert_called_once()
        self.assertTrue(app.exit_after_capture)
        self.assertFalse(app.ai.enabled.is_set())
        self.assertFalse(app.closing)
        app.tray.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
