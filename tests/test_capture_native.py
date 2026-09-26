from pathlib import Path
import ctypes as C
from ctypes import wintypes as W
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_native as native
REAL_API = native._api


class GeometryTests(unittest.TestCase):
    def setUp(self):
        # These are Windows API doubles even when the suite runs on macOS CI.
        platform = patch.object(native.sys, "platform", "win32")
        platform.start()
        self.addCleanup(platform.stop)

    def test_virtual_desktop_preserves_negative_origin(self):
        api = MagicMock()
        api.GetSystemMetrics.side_effect = [-1920, -200, 4480, 1640]
        with patch.object(native, "_api", return_value=api):
            self.assertEqual(native.desktop_bounds(), dict(x=-1920, y=-200, width=4480, height=1640))

    def test_window_info_includes_client_coordinates_and_large_handle(self):
        api = MagicMock()
        api.IsIconic.return_value = False
        def outer(_hwnd, rect):
            r = rect._obj
            r.left, r.top, r.right, r.bottom = -1200, 40, -390, 680
            return True
        def client(_hwnd, rect):
            r = rect._obj
            r.left, r.top, r.right, r.bottom = 0, 0, 800, 600
            return True
        def origin(_hwnd, point):
            point._obj.x, point._obj.y = -1195, 75
            return True
        def title(_hwnd, buffer, _size):
            buffer.value = "Self-owned fixture"
            return len(buffer.value)
        api.GetWindowRect.side_effect = outer
        api.GetClientRect.side_effect = client
        api.ClientToScreen.side_effect = origin
        api.GetWindowTextLengthW.return_value = 40
        api.GetWindowTextW.side_effect = title
        hwnd = 0x100ABCDEF
        with patch.object(native, "_api", return_value=api):
            result = native.window_info(hwnd)
        self.assertEqual(result["hwnd"], hwnd)
        self.assertEqual((result["x"], result["width"]), (-1200, 810))
        self.assertEqual((result["client_x"], result["client_width"], result["client_height"]), (-1195, 800, 600))
        api.GetWindowRect.assert_called_once()
        self.assertEqual(api.GetWindowRect.call_args.args[0], hwnd)

    def test_invalid_and_minimized_windows_rejected(self):
        api = MagicMock()
        api.IsIconic.return_value = True
        with patch.object(native, "_api", return_value=api):
            for hwnd in ("invalid", -1, 10):
                with self.assertRaises(ValueError):
                    native.window_info(hwnd)


@unittest.skipUnless(os.name == "nt", "Windows native callbacks")
class HotkeyTests(unittest.TestCase):
    def setUp(self):
        self.root = MagicMock()
        self.root.after.return_value = "after-key"
        self.api = MagicMock()
        self.api.RegisterClassW.return_value = 1
        self.api.CreateWindowExW.return_value = 0x100123456
        self.api.DefWindowProcW.return_value = 0
        self.api.PeekMessageW.return_value = False
        self.kernel = MagicMock()
        self.kernel.GetModuleHandleW.return_value = 99
        self.patches = [patch.object(native, "_api", return_value=self.api),
                        patch.object(native, "_user32", self.api),
                        patch.object(native, "_kernel32", self.kernel)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_conflict_reporting_and_only_owned_registration_removed(self):
        self.api.RegisterHotKey.side_effect = [True, False]
        manager = native.HotkeyManager(self.root, {"start_stop": lambda: None, "pause_resume": lambda: None})
        result = manager.start()
        self.assertTrue(result["start_stop"]["registered"])
        self.assertFalse(result["pause_resume"]["registered"])
        self.assertTrue(result["pause_resume"]["error"])
        hwnd = manager._hwnd
        manager.close()
        manager.close()
        self.api.UnregisterHotKey.assert_called_once_with(hwnd, 1)
        self.api.DestroyWindow.assert_called_once_with(hwnd)
        self.root.after_cancel.assert_called_once_with("after-key")

    def test_owned_window_messages_dispatch_on_tk_poll_only(self):
        callback = MagicMock()
        manager = native.HotkeyManager(self.root, {"screenshot": callback})
        self.addCleanup(manager.close)
        manager.start()
        manager._proc(manager._hwnd, 0x0312, 3, 0)
        manager._proc(manager._hwnd, 0x0312, 500, 0)
        callback.assert_not_called()
        manager._poll()
        callback.assert_called_once_with()
        args = self.api.PeekMessageW.call_args.args
        self.assertEqual(args[1:], (manager._hwnd, 0x0312, 0x0312, 1))
        self.api.DefWindowProcW.assert_called_once_with(manager._hwnd, 0x0312, 500, 0)

    def test_callback_can_close_manager_without_rescheduling(self):
        manager = native.HotkeyManager(self.root, {})
        manager.callbacks["start_stop"] = manager.close
        manager.start()
        manager._pending.append("start_stop")
        manager._poll()
        self.assertFalse(manager._running)
        self.root.after.assert_called_once()

    def test_native_signatures_keep_pointer_sized_handles(self):
        # Construct the real API without enumerating or capturing other windows.
        with patch.object(native, "_user32", None):
            api = REAL_API()
            self.assertIs(api.CreateWindowExW.restype, W.HWND)
            self.assertIs(api.GetAncestor.restype, W.HWND)
            self.assertEqual(C.sizeof(api._get_style.restype), C.sizeof(C.c_void_p))


if __name__ == "__main__":
    unittest.main()
