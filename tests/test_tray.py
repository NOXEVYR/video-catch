"""Win32 tray lifecycle without adding an icon to the real taskbar."""
import ctypes
from pathlib import Path
import queue
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tray


class FakeUser32:
    def __init__(self):
        self.messages = queue.Queue()
        self.calls = []
        self.wndproc = None
        self.fail_icon = False
        self.menu_choice = 0

    def RegisterWindowMessageW(self, name):
        self.calls.append(("register_message", name))
        return 0xC123

    def RegisterClassExW(self, pointer):
        self.wndproc = pointer._obj.lpfnWndProc
        self.calls.append(("register_class",))
        return 1

    def UnregisterClassW(self, name, module):
        self.calls.append(("unregister_class",))
        return 1

    def CreateWindowExW(self, *args):
        self.calls.append(("create_window", args[0], args[3]))
        return 101

    def DestroyWindow(self, hwnd):
        self.calls.append(("destroy_window",))
        return 1

    def DefWindowProcW(self, *args):
        return 0

    def LoadImageW(self, *args):
        self.calls.append(("load_icon",))
        return 0 if self.fail_icon else 202

    def DestroyIcon(self, icon):
        self.calls.append(("destroy_icon",))
        return 1

    def GetMessageW(self, pointer, *args):
        try:
            message, wparam, lparam = self.messages.get(timeout=3)
        except queue.Empty:
            return -1
        if message is None:
            return 0
        value = pointer._obj
        value.hwnd, value.message, value.wParam, value.lParam = 101, message, wparam, lparam
        return 1

    def TranslateMessage(self, pointer):
        return 1

    def DispatchMessageW(self, pointer):
        value = pointer._obj
        return self.wndproc(value.hwnd, value.message, value.wParam, value.lParam)

    def PostMessageW(self, hwnd, message, wparam, lparam):
        self.messages.put((message, wparam, lparam))
        return 1

    def PostQuitMessage(self, code):
        self.messages.put((None, 0, 0))

    def CreatePopupMenu(self):
        self.calls.append(("create_menu",))
        return 303

    def AppendMenuW(self, *args):
        self.calls.append(("append_menu", args[1], args[2]))
        return 1

    def DestroyMenu(self, menu):
        self.calls.append(("destroy_menu",))
        return 1

    def GetCursorPos(self, pointer):
        pointer._obj.x, pointer._obj.y = 10, 20
        return 1

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("foreground",))
        return 1

    def TrackPopupMenu(self, *args):
        return self.menu_choice

    def send(self, message, lparam=0):
        self.messages.put((message, 0, lparam))


class FakeKernel32:
    def GetModuleHandleW(self, name):
        return 404


class FakeShell32:
    def __init__(self):
        self.calls = []
        self.fail_action = None

    def Shell_NotifyIconW(self, action, pointer):
        data = pointer._obj
        self.calls.append((action, data.cbSize, data.hWnd, data.uVersion))
        return action != self.fail_action


class FakeAPI:
    def __init__(self):
        self.user32 = FakeUser32()
        self.kernel32 = FakeKernel32()
        self.shell32 = FakeShell32()


def until(predicate, seconds=2):
    end = time.monotonic() + seconds
    while not predicate() and time.monotonic() < end:
        time.sleep(0.005)
    if not predicate():
        raise AssertionError("worker did not reach expected state")


class TrayTests(unittest.TestCase):
    def test_native_structures_include_pointer_width_and_full_msg(self):
        if sys.platform != "win32":
            self.skipTest("Windows ABI")
        self.assertEqual(ctypes.sizeof(tray.WNDCLASSEXW), 80 if ctypes.sizeof(ctypes.c_void_p) == 8 else 48)
        self.assertEqual(ctypes.sizeof(tray.NOTIFYICONDATAW), 976 if ctypes.sizeof(ctypes.c_void_p) == 8 else 956)
        self.assertEqual(ctypes.sizeof(tray.MSG), 48 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32)
        self.assertEqual(tray.NOTIFYICONDATAW.uVersion.offset, 816 if ctypes.sizeof(ctypes.c_void_p) == 8 else 800)

    def test_add_events_menu_and_close_release_all_handles(self):
        api = FakeAPI()
        icon = tray.TrayIcon(_api=api)
        self.assertTrue(icon.start())
        self.assertTrue(icon.active)
        self.assertEqual([call[0] for call in api.shell32.calls], [tray.NIM_ADD, tray.NIM_SETVERSION])
        api.user32.send(tray.WM_TRAY, tray.WM_LBUTTONUP)
        self.assertEqual(icon.events.get(timeout=1), "show")
        api.user32.send(tray.WM_TRAY, tray.WM_LBUTTONDBLCLK)
        self.assertEqual(icon.events.get(timeout=1), "show")
        api.user32.send(tray.WM_TRAY, tray.NIN_KEYSELECT)
        self.assertEqual(icon.events.get(timeout=1), "show")
        api.user32.menu_choice = tray.MENU_LIBRARY
        api.user32.send(tray.WM_TRAY, tray.WM_CONTEXTMENU)
        self.assertEqual(icon.events.get(timeout=1), "library")
        icon._last_menu = 0
        api.user32.menu_choice = tray.MENU_SHOW
        api.user32.send(tray.WM_TRAY, tray.WM_RBUTTONUP)
        self.assertEqual(icon.events.get(timeout=1), "show")
        icon._last_menu = 0
        api.user32.menu_choice = tray.MENU_EXIT
        api.user32.send(tray.WM_TRAY, tray.WM_CONTEXTMENU)
        self.assertEqual(icon.events.get(timeout=1), "exit")
        icon.close()
        self.assertFalse(icon.active)
        self.assertEqual(api.shell32.calls[-1][0], tray.NIM_DELETE)
        names = [call[0] for call in api.user32.calls]
        self.assertLess(names.index("destroy_window"), names.index("unregister_class"))
        self.assertIn("destroy_icon", names)
        self.assertIn("destroy_menu", names)
        self.assertEqual([call[1] for call in api.user32.calls if call[0] == "append_menu"][:4],
                         [tray.MF_STRING, tray.MF_STRING, tray.MF_SEPARATOR, tray.MF_STRING])
        icon.close()
        self.assertTrue(icon.events.empty())

    def test_failed_start_cannot_authorize_hiding_and_cleans_up(self):
        for fail_at in ("load", tray.NIM_ADD, tray.NIM_SETVERSION):
            with self.subTest(fail_at=fail_at):
                api = FakeAPI()
                if fail_at == "load":
                    api.user32.fail_icon = True
                else:
                    api.shell32.fail_action = fail_at
                icon = tray.TrayIcon(_api=api)
                self.assertFalse(icon.start())
                self.assertFalse(icon.active)
                self.assertTrue(icon.error)
                names = [call[0] for call in api.user32.calls]
                self.assertIn("destroy_window", names)
                self.assertIn("unregister_class", names)
                if fail_at == tray.NIM_SETVERSION:
                    self.assertEqual([call[0] for call in api.shell32.calls],
                                     [tray.NIM_ADD, tray.NIM_SETVERSION, tray.NIM_DELETE])
                icon.close()

    def test_explorer_restart_reregisters_or_reports_loss(self):
        api = FakeAPI()
        icon = tray.TrayIcon(_api=api)
        self.assertTrue(icon.start())
        api.user32.send(0xC123)
        until(lambda: len(api.shell32.calls) == 4)
        self.assertTrue(icon.active)
        self.assertEqual([call[0] for call in api.shell32.calls[:4]],
                         [tray.NIM_ADD, tray.NIM_SETVERSION, tray.NIM_ADD, tray.NIM_SETVERSION])
        api.shell32.fail_action = tray.NIM_ADD
        api.user32.send(0xC123)
        self.assertEqual(icon.events.get(timeout=1), "unavailable")
        self.assertFalse(icon.active)
        icon.close()

    def test_unexpected_message_loop_exit_reports_loss(self):
        api = FakeAPI()
        icon = tray.TrayIcon(_api=api)
        self.assertTrue(icon.start())
        api.user32.send(None)
        self.assertEqual(icon.events.get(timeout=1), "unavailable")
        until(lambda: not icon.active)
        self.assertEqual(api.shell32.calls[-1][0], tray.NIM_DELETE)
        icon.close()


if __name__ == "__main__":
    unittest.main()
