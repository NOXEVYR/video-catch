"""Recording controls tested with synthetic devices and an inert recorder."""
import sys
import threading
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from recording_ui import RecordingPanel, RecordingToolbar, elapsed_text, panel_size, recording_options, window_label


INVENTORY = {"cameras": [{"name": "Test camera"}], "microphones": [{"index": 3, "name": "Test mic"}],
             "systems": [{"index": 7, "name": "Test output"}]}


def values(**extra):
    result = dict(mode="全屏录制", audio="不录声音", fps="30", quality="均衡", duration="0", cursor=True,
                  overlay=False, camera="Test camera", microphone="Test mic  [3]", system="Test output  [7]")
    result.update(extra)
    return result


class OptionTests(unittest.TestCase):
    def test_panel_fits_large_and_small_screens(self):
        self.assertEqual(panel_size(2560, 1440, 1.5, 1080)[:2], (960, 1100))
        width, height, min_width, min_height = panel_size(1280, 720, 1.5, 1080)
        self.assertLessEqual(width, 1220)
        self.assertLessEqual(height, 585)
        self.assertLessEqual(min_width, width)
        self.assertLessEqual(min_height, height)

    def test_safe_silent_defaults(self):
        self.assertEqual(recording_options(values(), {}), dict(mode="screen", audio="none", fps=30,
                                                              quality="balanced", duration=0, cursor=True))

    def test_region_negative_monitor_origin_preserved(self):
        region = dict(x=-1920, y=-120, width=640, height=480)
        options = recording_options(values(mode="区域录制"), {}, region)
        self.assertEqual(options["region"], region)
        self.assertIsNot(options["region"], region)

    def test_region_requires_selection(self):
        with self.assertRaisesRegex(ValueError, "框选"):
            recording_options(values(mode="区域录制"), {})

    def test_window_selection_uses_handle_not_ambiguous_title(self):
        windows = [dict(hwnd=42, title="Editor"), dict(hwnd=43, title="Editor")]
        options = recording_options(values(mode="窗口录制", window=window_label(windows[1])), {}, windows=windows)
        self.assertEqual(options["hwnd"], 43)
        with self.assertRaisesRegex(ValueError, "刷新"):
            recording_options(values(mode="窗口录制", window="closed"), {}, windows=windows)

    def test_camera_only_and_camera_overlay(self):
        self.assertEqual(recording_options(values(mode="仅摄像头"), INVENTORY)["camera"], "Test camera")
        self.assertEqual(recording_options(values(overlay=True), INVENTORY)["camera"], "Test camera")
        self.assertNotIn("camera", recording_options(values(), INVENTORY))
        with self.assertRaisesRegex(ValueError, "没有可用的摄像头"):
            recording_options(values(mode="仅摄像头"), {})

    def test_both_audio_devices_are_explicit(self):
        options = recording_options(values(audio="系统 + 麦克风"), INVENTORY)
        self.assertEqual((options["system_index"], options["microphone_index"]), (7, 3))
        with self.assertRaisesRegex(ValueError, "麦克风"):
            recording_options(values(audio="系统 + 麦克风"), {"systems": INVENTORY["systems"]})

    def test_duration_and_fps_validation(self):
        for duration in ("-1", "inf", "nan", "bad"):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                recording_options(values(duration=duration), {})
        with self.assertRaises(ValueError):
            recording_options(values(fps="120"), {})
        self.assertEqual(recording_options(values(duration="90.5"), {})["duration"], 90.5)
        self.assertEqual(elapsed_text(3661.2), "01:01:01")


class FakeRecorder:
    def __init__(self):
        self.is_busy = False
        self.state = {"status": "", "elapsed": 0}

    def snapshot(self):
        return dict(self.state)


def app_for(root):
    return SimpleNamespace(root=root, recorder=FakeRecorder(), notice=tk.StringVar(root), folder=tk.StringVar(root, value="test-output"),
                           record_hotkeys=tk.BooleanVar(root), record_hotkey_status=tk.StringVar(root),
                           start_recording=Mock(), pause_recording=Mock(), resume_recording=Mock(), stop_recording=Mock(),
                           capture_screenshot=Mock(), toggle_annotations=Mock(), toggle_record_hotkeys=Mock())


class CountdownTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tcl()
        self.app = app_for(self.root)
        self.panel = RecordingPanel(self.app)

    def tearDown(self):
        self.panel.close()

    def test_duplicate_countdown_start_and_close_cancel(self):
        self.panel.start()
        timer = self.panel._countdown
        self.panel.start()
        self.assertEqual(timer, self.panel._countdown)
        self.assertIsNotNone(self.panel._pending_options)
        self.panel.close()
        self.assertIsNone(self.panel._pending_options)
        self.panel._count_down(0)
        self.app.start_recording.assert_not_called()

    def test_immediate_start_and_busy_guard(self):
        self.panel.vars["countdown"].set("立即开始")
        self.panel.start()
        self.app.start_recording.assert_called_once()
        self.app.recorder.is_busy = True
        self.panel.start()
        self.app.start_recording.assert_called_once()

    def test_external_start_during_countdown_is_not_duplicated(self):
        self.panel.start()
        self.app.recorder.is_busy = True
        self.panel._count_down(0)
        self.app.start_recording.assert_not_called()

    def test_invalid_selection_is_user_facing(self):
        self.panel.vars["mode"].set("仅摄像头")
        self.panel.start()
        self.assertIn("摄像头", self.app.notice.get())
        self.app.start_recording.assert_not_called()

    def test_inventory_discovery_runs_outside_ui_thread(self):
        finished = threading.Event()
        release = threading.Event()
        ui_thread = threading.get_ident()
        calls = []

        def inventory():
            calls.append(threading.get_ident())
            finished.set()
            release.wait(2)
            return INVENTORY

        with patch.dict(sys.modules, {"recorder": SimpleNamespace(device_inventory=inventory)}):
            self.panel.refresh_devices()
            self.assertTrue(finished.wait(2))
            release.set()
            result = self.panel._results.get(timeout=2)
        self.assertEqual(result, INVENTORY)
        self.assertNotEqual(calls, [ui_thread])


class NativeWidgetTests(unittest.TestCase):
    """Build real Tk widgets, but stub every capture/device/native interaction."""
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError:
            self.skipTest("Tk display not available")
        self.app = app_for(self.root)
        self.panel = RecordingPanel(self.app)
        self.panel._build()
        self.panel.inventory = INVENTORY
        self.panel._apply_devices()
        self.panel.refresh()
        self.toolbar = RecordingToolbar(self.app)

    def tearDown(self):
        self.toolbar.close()
        self.panel.close()
        self.root.destroy()

    def test_controls_lock_and_pause_resume_state(self):
        self.app.recorder.is_busy = True
        self.app.recorder.state = {"status": "录制暂停", "elapsed": 12}
        self.panel.refresh()
        self.assertEqual(str(self.panel.mode_box.cget("state")), "disabled")
        self.assertEqual(self.panel.pause_button.cget("text"), "继续")
        self.assertEqual(str(self.panel.devices_button.cget("state")), "disabled")
        self.panel._pause_resume()
        self.app.resume_recording.assert_called_once()
        self.app.recorder.state["status"] = "正在保存"
        self.panel.refresh()
        self.assertEqual(str(self.panel.stop_button.cget("state")), "disabled")

    def test_show_reuses_hidden_panel(self):
        window = self.panel.window
        window.withdraw()
        self.panel.show()
        self.assertIs(self.panel.window, window)

    def test_cancel_region_preserves_previous_selection(self):
        self.panel.region = dict(x=1, y=2, width=300, height=200)
        old = dict(self.panel.region)
        callbacks = []

        def select_region(root, callback):
            callbacks.append(callback)
            return SimpleNamespace(active=True, close=lambda: None)

        with patch.dict(sys.modules, {"capture_overlays": SimpleNamespace(select_region=select_region)}):
            self.panel._select_region()
            callbacks[0](None)
        self.assertEqual(self.panel.region, old)
        self.assertIsNone(self.panel.selector)

    def test_toolbar_visible_until_saving_finishes(self):
        self.app.recorder.is_busy = True
        self.app.recorder.state = {"status": "正在保存", "elapsed": 5}
        exclude = Mock(return_value=True)
        with patch.dict(sys.modules, {"capture_native": SimpleNamespace(exclude_from_capture=exclude)}):
            self.toolbar.refresh()
        self.assertTrue(self.toolbar.window.winfo_exists())
        exclude.assert_called_once()
        self.assertEqual(str(self.toolbar.stop_button.cget("state")), "disabled")
        self.app.recorder.is_busy = False
        self.toolbar.refresh()
        self.assertIsNone(self.toolbar.window)

    def test_screenshot_does_not_require_audio_or_camera_device(self):
        self.panel.vars["audio"].set("系统 + 麦克风")
        self.panel.vars["overlay"].set(True)
        self.panel.inventory = {}
        callbacks = []
        with patch.object(self.root, "after", side_effect=lambda _, callback: callbacks.append(callback)):
            self.panel._screenshot()
        callbacks[0]()
        options = self.app.capture_screenshot.call_args.args[0]
        self.assertEqual(options["audio"], "none")
        self.assertNotIn("camera", options)

    def test_async_screenshot_keeps_panel_hidden_until_capture_finishes(self):
        self.app.screenshot_pending = True
        self.panel.window.withdraw()
        self.panel._finish_screenshot(dict(mode="screen"), restore=True)
        self.assertFalse(self.panel.visible)
        self.panel.refresh()
        self.assertFalse(self.panel.visible)
        self.panel.screenshot()
        self.panel.show()
        self.assertFalse(self.panel.visible)
        self.app.capture_screenshot.assert_called_once()
        self.app.screenshot_pending = False
        self.panel.refresh()
        self.assertTrue(self.panel.visible)

    def test_recording_does_not_iconify_already_hidden_root(self):
        self.panel.vars["countdown"].set("立即开始")
        with patch.object(self.root, "iconify") as iconify:
            self.panel.start()
        iconify.assert_not_called()
        self.app.start_recording.assert_called_once()

    def test_small_window_keeps_footer_and_scrolls_settings(self):
        self.panel.window.minsize(660, 480)
        self.panel.window.geometry("780x610")
        self.root.update()
        footer = self.panel.start_button.master
        self.assertLessEqual(footer.winfo_y() + footer.winfo_height(), self.panel.window.winfo_height())
        self.assertGreater(self.panel.body.winfo_reqheight(), self.panel.canvas.winfo_height())
        old = self.panel.vars["mode"].get()
        self.panel.mode_box.event_generate("<MouseWheel>", delta=-120)
        self.assertEqual(self.panel.vars["mode"].get(), old)
        self.assertGreater(self.panel.canvas.yview()[0], 0)

    def test_close_cancels_screenshot_and_ignores_stale_selector_callback(self):
        callbacks = []

        def select_region(root, callback):
            callbacks.append(callback)
            return SimpleNamespace(active=True, close=lambda: None)

        with patch.dict(sys.modules, {"capture_overlays": SimpleNamespace(select_region=select_region)}):
            self.panel._select_region()
        self.panel.close()
        callbacks[0](dict(x=1, y=2, width=100, height=100))
        self.assertIsNone(self.panel.region)
        self.panel._build()
        self.panel.screenshot()
        timer = self.panel._screenshot_timer
        self.assertIsNotNone(timer)
        self.panel.close()
        self.assertNotIn(timer, self.root.tk.call("after", "info"))
        self.app.capture_screenshot.assert_not_called()

    def test_close_after_external_window_destruction_is_idempotent(self):
        self.panel.start()
        self.panel.window.destroy()
        self.panel.close()
        self.panel.close()
        self.assertIsNone(self.panel._countdown)
        self.assertIsNone(self.panel._pending_options)
        self.app.start_recording.assert_not_called()

    def test_cleanup_after_root_destroyed(self):
        root = tk.Tk()
        root.withdraw()
        app = app_for(root)
        panel = RecordingPanel(app)
        panel._build()
        panel.start()
        toolbar = RecordingToolbar(app)
        root.destroy()
        panel.close()
        panel.refresh()
        toolbar.close()
        toolbar.refresh()


if __name__ == "__main__":
    unittest.main()
