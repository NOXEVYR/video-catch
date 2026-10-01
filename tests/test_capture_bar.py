"""Quick capture controls with inert backends; never samples the desktop."""
from pathlib import Path
import struct
import sys
import tempfile
import time
import tkinter as tk
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_bar import CaptureBar, _WindowsConfirmRegion
from capture_preferences import CapturePreferences, DEFAULTS
from recording_ui import window_label


def synthetic_png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
            + chunk(b"IEND", b""))


class FakeRecorder:
    def __init__(self):
        self.is_busy = False
        self.state = {"status": "准备就绪", "elapsed": 0}

    def snapshot(self):
        return dict(self.state)


class CaptureBarTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.recorder = FakeRecorder()
        self.app = SimpleNamespace(root=self.root, recorder=self.recorder, recording_panel=None,
                                   capture_bar=None, local_state=SimpleNamespace(data_dir=".", enabled=False),
                                   notice=tk.StringVar(self.root), screenshot_pending=False,
                                   store=SimpleNamespace(items={}), exit_after_capture=False,
                                   start_recording=Mock(), pause_recording=Mock(), resume_recording=Mock(),
                                   stop_recording=Mock(), capture_screenshot=Mock(), toggle_annotations=Mock())
        self.device_patch = patch("recording_ui.RecordingPanel.refresh_devices")
        self.device_patch.start()

    def tearDown(self):
        if self.app.capture_bar:
            self.app.capture_bar.close()
        if self.app.recording_panel:
            self.app.recording_panel.close()
        self.root.destroy()
        self.device_patch.stop()

    def make_bar(self, mode="record"):
        bar = self.app.capture_bar = CaptureBar(self.app, mode=mode)
        bar.show(mode)
        self.root.update_idletasks()
        return bar

    def test_record_starts_hidden_and_uses_existing_options_and_transport(self):
        bar = self.make_bar()
        self.assertTrue(bar.visible)
        self.assertEqual(bar.range_var.get(), "全屏")
        bar.panel.vars["countdown"].set("立即开始")
        def started(_options):
            self.recorder.is_busy = True
            self.recorder.state["status"] = "录制中"
        self.app.start_recording.side_effect = started
        bar.start()
        self.app.start_recording.assert_called_once()
        self.assertEqual(self.app.start_recording.call_args.args[0]["mode"], "screen")
        self.assertFalse(bar.visible)
        bar.show()
        self.assertTrue(bar.visible)
        self.assertTrue(bar.pause_button.winfo_manager())
        bar._pause_resume()
        self.app.pause_recording.assert_called_once()
        bar._stop()
        self.app.stop_recording.assert_called_once()

    def test_countdown_cancel_never_starts(self):
        bar = self.make_bar()
        bar.start()
        self.assertIsNotNone(bar._pending_options)
        bar._primary()
        self.assertIsNone(bar._pending_options)
        self.app.start_recording.assert_not_called()

    def test_settings_cancels_countdown_and_cannot_open_during_recording(self):
        bar = self.make_bar()
        bar.panel.show = Mock()
        bar.start()
        self.assertIsNotNone(bar._pending_options)
        bar.open_settings()
        self.assertIsNone(bar._pending_options)
        bar.panel.show.assert_called_once()
        self.recorder.is_busy = True
        bar.open_settings()
        bar.panel.show.assert_called_once()
        self.assertIn("正在采集", bar.status.get())

    def test_region_cancel_returns_bar_without_task(self):
        bar = self.make_bar("screenshot")
        callback = []
        selector = SimpleNamespace(active=True, close=Mock())
        with patch("capture_bar.select_confirm_region", side_effect=lambda _root, selected: callback.append(selected) or selector):
            bar.select_region()
        self.assertFalse(bar.visible)
        self.assertIs(bar.panel.selector, selector)
        selector.active = False
        callback[0](None)
        self.assertTrue(bar.visible)
        self.assertIsNone(bar.panel.region)
        self.app.capture_screenshot.assert_not_called()
        self.assertEqual(self.app.notice.get(), "")

    def test_screenshot_requires_selection_then_hides_before_capture(self):
        bar = self.make_bar("screenshot")
        callback = []
        selector = SimpleNamespace(active=True, close=Mock())
        with patch("capture_bar.select_confirm_region", side_effect=lambda _root, selected: callback.append(selected) or selector):
            bar.screenshot()
        self.app.capture_screenshot.assert_not_called()
        selector.active = False
        callback[0](dict(x=-100, y=50, width=300, height=200))
        def captured(options):
            self.assertFalse(bar.visible)
            self.assertEqual(options["region"]["x"], -100)
            self.app.screenshot_pending = True
            return {"id": "shot-1"}
        self.app.capture_screenshot.side_effect = captured
        bar.screenshot()
        time.sleep(.22)
        self.root.update()
        self.assertTrue(self.app.screenshot_pending)
        self.app.store.items["shot-1"] = {"status": "失败", "error": "synthetic failure"}
        self.app.screenshot_pending = False
        bar.refresh()
        self.assertTrue(bar.visible)
        self.assertIn("synthetic failure", bar.status.get())

    def test_screenshot_success_creates_isolated_png_and_stays_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "synthetic.png"
            bar = self.make_bar("screenshot")
            bar.panel.region = dict(x=10, y=20, width=50, height=40)
            png = synthetic_png()
            def captured(_options):
                self.assertFalse(bar.visible)
                output.write_bytes(png)
                self.app.store.items["shot-2"] = {"status": "截图中", "path": str(output)}
                self.app.screenshot_pending = True
                return {"id": "shot-2"}
            self.app.capture_screenshot.side_effect = captured
            bar.screenshot()
            time.sleep(.22)
            self.root.update()
            self.app.store.items["shot-2"]["status"] = "已保存"
            self.app.screenshot_pending = False
            bar.refresh()
            self.assertTrue(output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            image = tk.PhotoImage(master=self.root, file=str(output))
            self.assertEqual((image.width(), image.height()), (1, 1))
            self.assertFalse(bar.visible)

    def test_closed_window_is_rejected_instead_of_switching_target(self):
        bar = self.make_bar()
        target = {"hwnd": 123, "title": "Target"}
        with patch("capture_native.list_windows", return_value=[target]):
            bar.range_var.set("窗口")
            bar._range_changed()
        bar.panel.vars["window"].set(window_label(target))
        self.assertEqual(bar.panel.vars["window"].get(), window_label(target))
        self.assertEqual(bar.panel.get_options()["hwnd"], 123)
        with patch("capture_native.list_windows", return_value=[]):
            bar.start()
        self.app.start_recording.assert_not_called()
        self.assertIn("已关闭", bar.status.get())

    def test_record_and_screenshot_range_preferences_are_separate(self):
        bar = self.make_bar()
        self.assertLessEqual(bar.window.winfo_reqwidth(), 720)
        bar.range_var.set("全屏")
        bar._range_changed()
        bar.show("screenshot")
        self.assertEqual(bar.range_var.get(), "区域")
        bar.show("record")
        self.assertEqual(bar.range_var.get(), "全屏")

    def test_capture_settings_survive_panel_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            self.app.local_state = SimpleNamespace(data_dir=directory, enabled=True)
            bar = self.make_bar()
            bar.panel.vars["fps"].set("60")
            bar.panel.vars["quality"].set("清晰优先")
            bar.range_var.set("全屏")
            bar._range_changed()
            bar.show("screenshot")
            bar.close()
            bar.panel.close()
            self.app.capture_bar = self.app.recording_panel = None
            recreated = CaptureBar(self.app, mode="record")
            self.app.capture_bar = recreated
            self.assertEqual(recreated.panel.vars["fps"].get(), "60")
            self.assertEqual(recreated.panel.vars["quality"].get(), "清晰优先")
            self.assertEqual(recreated.panel.record_mode, "全屏录制")
            self.assertEqual(recreated.panel.screenshot_mode, "区域录制")
            recreated.close()
            recreated.panel.close()
            self.app.capture_bar = self.app.recording_panel = None

    def test_selector_release_needs_enter_and_can_redraw(self):
        selector = object.__new__(_WindowsConfirmRegion)
        selector.bounds = dict(x=-50, y=0, width=200, height=150)
        selector._start = (-40, 10)
        selector._preview = None
        selector.canvas = Mock()
        selector.window = Mock()
        selector._box = selector._label = 1
        selector._finish = Mock()
        selector._release(SimpleNamespace(x_root=40, y_root=60))
        selector._finish.assert_not_called()
        self.assertEqual(selector._preview["width"], 80)
        selector._confirm()
        selector._finish.assert_called_once_with(dict(x=-40, y=10, width=80, height=50))


class CapturePreferencesTests(unittest.TestCase):
    def test_round_trip_has_only_allowlisted_fields_and_no_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs = CapturePreferences(directory)
            values = dict(DEFAULTS, fps="60", audio="麦克风")
            self.assertTrue(prefs.save(values))
            raw = (Path(directory) / "capture-preferences.json").read_text(encoding="utf-8")
            self.assertEqual(CapturePreferences(directory).load()["fps"], "60")
            self.assertNotIn("path", raw)
            self.assertFalse(prefs.save(dict(values, token="secret")))
            self.assertNotIn("secret", (Path(directory) / "capture-preferences.json").read_text(encoding="utf-8"))

    def test_corruption_is_backed_up_and_disabled_store_never_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture-preferences.json"
            path.write_text("{bad", encoding="utf-8")
            prefs = CapturePreferences(directory)
            self.assertEqual(prefs.load(), DEFAULTS)
            backups = list(Path(directory).glob("capture-preferences.corrupt-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "{bad")
            self.assertTrue(prefs.save(DEFAULTS))
        with tempfile.TemporaryDirectory() as directory:
            prefs = CapturePreferences(directory, enabled=False)
            self.assertTrue(prefs.save(DEFAULTS))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_backup_failure_keeps_corrupt_original_and_blocks_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture-preferences.json"
            path.write_text("{bad", encoding="utf-8")
            prefs = CapturePreferences(directory)
            with patch("capture_preferences.os.replace", side_effect=PermissionError):
                self.assertEqual(prefs.load(), DEFAULTS)
                self.assertFalse(prefs.save(DEFAULTS))
            self.assertEqual(path.read_text(encoding="utf-8"), "{bad")


if __name__ == "__main__":
    unittest.main()
