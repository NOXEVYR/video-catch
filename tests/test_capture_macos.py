"""macOS contract tests run with framework doubles on every build platform."""
import ctypes as C
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_macos as mac
from capture_overlays import _DesktopCanvas, MacRegionSelector


def rect(x, y, width, height):
    return NS(origin=NS(x=x, y=y), size=NS(width=width, height=height))


class MacDisplayTests(unittest.TestCase):
    def setUp(self):
        self.quartz, self.cocoa = MagicMock(), MagicMock()
        self.quartz.CGGetActiveDisplayList.return_value = (0, [101, 202], 2)
        self.quartz.CGMainDisplayID.return_value = 202
        self.quartz.CGDisplayBounds.side_effect = lambda display: rect(-1920, -100, 1920, 1080) if display == 101 else rect(0, 0, 1512, 982)
        self.quartz.CGDisplayCopyDisplayMode.side_effect = lambda display: display
        self.quartz.CGDisplayModeGetPixelWidth.side_effect = lambda display: 1920 if display == 101 else 3024
        self.quartz.CGDisplayModeGetPixelHeight.side_effect = lambda display: 1080 if display == 101 else 1964
        self.frameworks = patch.object(mac, "_frameworks", return_value=(self.quartz, self.cocoa))
        self.frameworks.start()
        self.addCleanup(self.frameworks.stop)

    def test_monitor_ordinals_match_active_list_and_retina_is_separate(self):
        monitors = mac.list_monitors()
        self.assertEqual([m["id"] for m in monitors], [101, 202])
        self.assertEqual([m["display_index"] for m in monitors], [0, 1])
        self.assertFalse(monitors[0]["primary"])
        self.assertTrue(monitors[1]["primary"])
        self.assertEqual((monitors[1]["width"], monitors[1]["pixel_width"], monitors[1]["scale_x"]), (1512, 3024, 2))
        self.assertEqual(mac.desktop_bounds(), dict(x=-1920, y=-100, width=3432, height=1082))

    def test_permission_query_is_quiet_and_explicit_request_prompts(self):
        self.quartz.CGPreflightScreenCaptureAccess.return_value = False
        self.quartz.CGRequestScreenCaptureAccess.return_value = False
        self.assertFalse(mac.screen_capture_permission())
        self.quartz.CGRequestScreenCaptureAccess.assert_not_called()
        with self.assertRaises(PermissionError):
            mac.require_screen_capture_permission(request=True)
        self.quartz.CGRequestScreenCaptureAccess.assert_called_once()
        self.quartz.CGPreflightScreenCaptureAccess.return_value = True
        mac.require_screen_capture_permission(request=True)
        self.quartz.CGRequestScreenCaptureAccess.assert_called_once()

    def test_cross_screen_selection_is_rejected(self):
        self.assertEqual(mac.monitor_for_bounds(dict(x=-1000, y=0, width=800, height=600))["id"], 101)
        with self.assertRaisesRegex(ValueError, "同一显示器"):
            mac.monitor_for_bounds(dict(x=-100, y=100, width=400, height=400))

    def test_visible_window_metadata_and_filters(self):
        rows = [dict(kCGWindowNumber=42, kCGWindowOwnerPID=99999, kCGWindowOwnerName="Fixture app",
                     kCGWindowName="Fixture", kCGWindowBounds=dict(X=100, Y=100, Width=400, Height=300)),
                dict(kCGWindowNumber=43, kCGWindowOwnerPID=os.getpid(), kCGWindowBounds=dict(X=100, Y=100, Width=100, Height=100)),
                dict(kCGWindowNumber=44, kCGWindowLayer=20, kCGWindowBounds=dict(X=0, Y=0, Width=100, Height=100))]
        with patch.object(mac, "_window_rows", return_value=rows):
            windows = mac.list_windows()
            self.assertEqual([w["hwnd"] for w in windows], [42])
            result = mac.window_info(42)
            self.assertEqual((result["display_id"], result["display_index"], result["scale_x"]), (202, 1, 2))
            self.assertEqual((result["screen_width"], result["pixel_width"]), (1512, 3024))
            self.assertEqual((result["client_x"], result["client_width"]), (100, 400))
            with self.assertRaises(ValueError):
                mac.window_info(999)

    def test_cross_screen_window_remains_selectable_with_explicit_metadata(self):
        row = dict(kCGWindowNumber=42, kCGWindowBounds=dict(X=-100, Y=50, Width=500, Height=300))
        with patch.object(mac, "_window_rows", return_value=[row]):
            result = mac.window_info(42)
            self.assertTrue(result["spans_displays"])
            self.assertIsNone(result["display_index"])

    def test_placement_converts_top_left_to_cocoa_bottom_left(self):
        window, native = MagicMock(), MagicMock()
        self.cocoa.NSMakeRect.side_effect = lambda *args: args
        with patch.object(mac, "_nswindow", return_value=native):
            mac.place_window(window, dict(x=-1600, y=-80, width=500, height=300))
        native.setFrame_display_.assert_called_once_with((-1600., 762., 500., 300.), True)

    def test_exclusion_never_claims_modern_capture_is_protected(self):
        native = MagicMock()
        with patch.object(mac, "_nswindow", return_value=native):
            self.assertFalse(mac.exclude_from_capture(MagicMock()))
            native.setSharingType_.assert_called_once_with(0)


class MacHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.root, self.carbon = MagicMock(), MagicMock()
        self.root.after.return_value = "timer"
        self.carbon.GetApplicationEventTarget.return_value = 101
        def install(_target, _proc, _count, _events, _data, ref):
            ref._obj.value = 200
            return 0
        def register(_key, _modifiers, ident, _target, _options, ref):
            ref._obj.value = 300 + ident.id
            return 0
        self.carbon.InstallEventHandler.side_effect = install
        self.carbon.RegisterEventHotKey.side_effect = register
        self.carbon.GetEventKind.return_value = 5
        patcher = patch.object(mac, "_carbon_api", return_value=self.carbon)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_real_registration_result_and_owned_cleanup(self):
        def register(_key, _mods, ident, _target, _options, ref):
            if ident.id == 1:
                ref._obj.value = 301
                return 0
            return -9878
        self.carbon.RegisterEventHotKey.side_effect = register
        manager = mac.HotkeyManager(self.root, {"start_stop": lambda: None, "screenshot": lambda: None})
        status = manager.start()
        self.assertTrue(status["start_stop"]["registered"])
        self.assertFalse(status["screenshot"]["registered"])
        self.assertIn("-9878", status["screenshot"]["error"])
        manager.close()
        manager.close()
        self.carbon.UnregisterEventHotKey.assert_called_once()
        self.assertEqual(self.carbon.UnregisterEventHotKey.call_args.args[0].value, 301)
        self.carbon.RemoveEventHandler.assert_called_once()
        self.root.after_cancel.assert_called_once_with("timer")

    def test_owned_chords_are_deferred_and_repeats_suppressed(self):
        callback = MagicMock()
        manager = mac.HotkeyManager(self.root, {"annotate": callback})
        self.addCleanup(manager.close)
        manager.start()
        def read(_event, _name, _type, _actual, _size, _actual_size, out):
            out._obj.signature, out._obj.id = manager._signature, 4
            return 0
        self.carbon.GetEventParameter.side_effect = read
        manager._proc(None, 55, None)
        manager._proc(None, 55, None)
        callback.assert_not_called()
        manager._poll()
        callback.assert_called_once()
        self.carbon.GetEventKind.return_value = 6
        manager._proc(None, 55, None)
        self.carbon.GetEventKind.return_value = 5
        manager._proc(None, 55, None)
        manager._poll()
        self.assertEqual(callback.call_count, 2)

    def test_failed_handler_reports_unavailable_without_claiming_registration(self):
        self.carbon.InstallEventHandler.side_effect = lambda *_args: -1
        manager = mac.HotkeyManager(self.root, {"start_stop": lambda: None})
        status = manager.start()
        self.assertFalse(status["start_stop"]["registered"])
        self.carbon.RegisterEventHotKey.assert_not_called()
        self.root.after.assert_not_called()


class MacOverlayGeometryTests(unittest.TestCase):
    def test_cross_monitor_stroke_fans_out_and_undo_removes_every_copy(self):
        first, second = MagicMock(), MagicMock()
        first.create_line.return_value, second.create_line.return_value = 10, 20
        desktop = dict(x=-1920, y=-100, width=3432, height=1082)
        canvas = _DesktopCanvas([(None, first, dict(x=-1920, y=-100)),
                                 (None, second, dict(x=0, y=0))], desktop)
        item = canvas.create_line(1900, 150, 2050, 150, width=5)
        first.create_line.assert_called_once_with(1900, 150, 2050, 150, width=5)
        second.create_line.assert_called_once_with(-20, 50, 130, 50, width=5)
        canvas.delete(item)
        first.delete.assert_called_once_with(10)
        second.delete.assert_called_once_with(20)

    def test_mac_selector_closes_all_screens_before_deferred_callback(self):
        selector = MacRegionSelector.__new__(MacRegionSelector)
        selector.root, selector.window, extra = MagicMock(), MagicMock(), MagicMock()
        selector.active = True
        selector.on_selected = MagicMock()
        callback = selector.on_selected
        selector._surfaces = [(selector.window, None, None, None, None), (extra, None, None, None, None)]
        selector.close()
        selector.close()
        extra.destroy.assert_called_once()
        selector.window.destroy.assert_called_once()
        callback.assert_not_called()
        selector.root.after.call_args.args[1]()
        callback.assert_called_once_with(None)


if __name__ == "__main__":
    unittest.main()
