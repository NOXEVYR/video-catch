"""Windows notification icon, isolated from Tk's thread.

Integration: call ``start()`` before hiding the main window and hide only if it
returns True and ``active`` is True. Poll ``events`` on Tk's thread; values are
``show``, ``library``, ``exit``, and ``unavailable`` (restore a hidden window).
Call ``close()`` while shutting down. No method here calls Tk.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import os
from pathlib import Path
import queue
import sys
import threading
import time


WM_APP = 0x8000
WM_USER = 0x0400
WM_TRAY = WM_APP + 1
WM_NULL = 0
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIN_SELECT = WM_USER
NIN_KEYSELECT = WM_USER + 1
NIM_ADD = 0
NIM_DELETE = 2
NIM_SETVERSION = 4
NOTIFYICON_VERSION_4 = 4
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
NIF_SHOWTIP = 0x80
IMAGE_ICON = 1
LR_DEFAULTSIZE = 0x40
LR_LOADFROMFILE = 0x10
WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x80
MF_STRING = 0
MF_SEPARATOR = 0x800
TPM_RIGHTBUTTON = 2
TPM_RETURNCMD = 0x100
MENU_SHOW = 1001
MENU_LIBRARY = 1002
MENU_EXIT = 1003
ICON_ID = 1

WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(wt.LPARAM, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    # uVersion occupies the same storage as the documented uTimeout union.
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
                ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", wt.POINT),
                ("lPrivate", wt.DWORD)]


class _Win32:
    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        u, k, s = self.user32, self.kernel32, self.shell32

        def bind(lib, name, result, *args):
            function = getattr(lib, name)
            function.restype = result
            function.argtypes = list(args)

        bind(k, "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)
        bind(u, "RegisterClassExW", wt.ATOM, ctypes.POINTER(WNDCLASSEXW))
        bind(u, "UnregisterClassW", wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
        bind(u, "CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR,
             wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
             wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
        bind(u, "DestroyWindow", wt.BOOL, wt.HWND)
        bind(u, "DefWindowProcW", wt.LPARAM, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        bind(u, "RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)
        bind(u, "LoadImageW", wt.HANDLE, wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
             ctypes.c_int, ctypes.c_int, wt.UINT)
        bind(u, "DestroyIcon", wt.BOOL, wt.HICON)
        bind(s, "Shell_NotifyIconW", wt.BOOL, wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
        bind(u, "GetMessageW", ctypes.c_int, ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT)
        bind(u, "TranslateMessage", wt.BOOL, ctypes.POINTER(MSG))
        bind(u, "DispatchMessageW", wt.LPARAM, ctypes.POINTER(MSG))
        bind(u, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        bind(u, "PostQuitMessage", None, ctypes.c_int)
        bind(u, "CreatePopupMenu", wt.HMENU)
        bind(u, "AppendMenuW", wt.BOOL, wt.HMENU, wt.UINT, wt.WPARAM, wt.LPCWSTR)
        bind(u, "DestroyMenu", wt.BOOL, wt.HMENU)
        bind(u, "GetCursorPos", wt.BOOL, ctypes.POINTER(wt.POINT))
        bind(u, "SetForegroundWindow", wt.BOOL, wt.HWND)
        bind(u, "TrackPopupMenu", wt.UINT, wt.HMENU, wt.UINT, ctypes.c_int,
             ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID)


def _win_error(step):
    return OSError(f"{step} failed (Win32 {ctypes.get_last_error()})")


class TrayIcon:
    """One icon with an independent Win32 message loop and queue-only callbacks."""

    def __init__(self, icon_path=None, title="拾影 VideoCatch", *, _api=None):
        assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets"
        self.icon_path = Path(icon_path) if icon_path is not None else assets / "videocatch.ico"
        self.title = title[:127]
        self.events = queue.Queue()
        self.error = ""
        self._api = _api
        self._active = threading.Event()
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._hwnd = None
        self._icon = None
        self._nid = None
        self._class_name = None
        self._module = None
        self._taskbar_created = 0
        self._last_menu = 0.0
        self._wndproc = None

    @property
    def active(self):
        return self._active.is_set() and self._thread is not None and self._thread.is_alive()

    def start(self, timeout=4.0):
        """Return True only after Shell_NotifyIcon has installed a usable icon."""
        if sys.platform != "win32" and self._api is None:
            self.error = "Windows notification area unavailable"
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.active
            self._ready.clear()
            self._stopping.clear()
            self._active.clear()
            self.error = ""
            self._thread = threading.Thread(target=self._run, name="VideoCatch tray", daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout):
            self.error = "notification area startup timed out"
            self.close()
            return False
        return self.active

    def close(self, timeout=4.0):
        """Request worker cleanup, including NIM_DELETE and DestroyIcon."""
        self._stopping.set()
        thread = self._thread
        if thread is None:
            return
        hwnd = self._hwnd
        if hwnd and self._api is not None:
            self._api.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if thread is not threading.current_thread():
            thread.join(timeout)
        if not thread.is_alive():
            self._thread = None
            self._active.clear()

    def _add_icon(self):
        s = self._api.shell32.Shell_NotifyIconW
        if not s(NIM_ADD, ctypes.byref(self._nid)):
            return False
        self._nid.uVersion = NOTIFYICON_VERSION_4
        if not s(NIM_SETVERSION, ctypes.byref(self._nid)):
            s(NIM_DELETE, ctypes.byref(self._nid))
            return False
        return True

    def _run(self):
        registered = False
        added = False
        was_active = False
        try:
            if self._api is None:
                self._api = _Win32()
            u = self._api.user32
            k = self._api.kernel32
            self._taskbar_created = u.RegisterWindowMessageW("TaskbarCreated")
            if not self._taskbar_created:
                raise _win_error("RegisterWindowMessageW")
            self._module = k.GetModuleHandleW(None)
            if not self._module:
                raise _win_error("GetModuleHandleW")
            self._wndproc = WNDPROC(self._window_proc)
            self._class_name = f"VideoCatchTray_{os.getpid()}_{id(self):x}"
            cls = WNDCLASSEXW()
            cls.cbSize = ctypes.sizeof(cls)
            cls.lpfnWndProc = self._wndproc
            cls.hInstance = self._module
            cls.lpszClassName = self._class_name
            if not u.RegisterClassExW(ctypes.byref(cls)):
                raise _win_error("RegisterClassExW")
            registered = True
            self._hwnd = u.CreateWindowExW(WS_EX_TOOLWINDOW, self._class_name,
                self.title, WS_POPUP, 0, 0, 0, 0, None, None, self._module, None)
            if not self._hwnd:
                raise _win_error("CreateWindowExW")
            self._icon = u.LoadImageW(None, str(self.icon_path), IMAGE_ICON, 0, 0,
                                     LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if not self._icon:
                raise _win_error("LoadImageW")
            self._nid = NOTIFYICONDATAW()
            self._nid.cbSize = ctypes.sizeof(self._nid)
            self._nid.hWnd = self._hwnd
            self._nid.uID = ICON_ID
            self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
            self._nid.uCallbackMessage = WM_TRAY
            self._nid.hIcon = self._icon
            self._nid.szTip = self.title
            if not self._add_icon():
                raise _win_error("Shell_NotifyIconW")
            added = True
            was_active = True
            self._active.set()
            self._ready.set()
            message = MSG()
            while not self._stopping.is_set():
                value = u.GetMessageW(ctypes.byref(message), None, 0, 0)
                if value == -1:
                    raise _win_error("GetMessageW")
                if value == 0:
                    break
                u.TranslateMessage(ctypes.byref(message))
                u.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._active.clear()
            if added and self._nid is not None:
                self._api.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            if self._hwnd:
                self._api.user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            if self._icon:
                self._api.user32.DestroyIcon(self._icon)
                self._icon = None
            if registered:
                self._api.user32.UnregisterClassW(self._class_name, self._module)
            self._wndproc = None
            if was_active and not self._stopping.is_set():
                self.events.put("unavailable")
            self._ready.set()

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == self._taskbar_created:
                self._active.clear()
                if self._add_icon():
                    self._active.set()
                else:
                    self.events.put("unavailable")
                return 0
            if message == WM_TRAY:
                event = int(lparam) & 0xFFFF  # NOTIFYICON_VERSION_4 LOWORD(lParam)
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, NIN_SELECT, NIN_KEYSELECT):
                    self.events.put("show")
                elif event in (WM_CONTEXTMENU, WM_RBUTTONUP):
                    now = time.monotonic()
                    if now - self._last_menu > 0.3:
                        self._last_menu = now
                        self._show_menu(hwnd)
                return 0
            if message in (WM_CLOSE, WM_DESTROY):
                self._api.user32.PostQuitMessage(0)
                return 0
        except Exception as exc:
            # ctypes callbacks cannot propagate exceptions; keep the window reachable.
            self.error = f"{type(exc).__name__}: {exc}"
        return self._api.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self, hwnd):
        u = self._api.user32
        menu = u.CreatePopupMenu()
        if not menu:
            return
        try:
            entries = ((MENU_SHOW, "显示主窗口"), (MENU_LIBRARY, "视频素材库"),
                       (MENU_EXIT, "退出拾影"))
            for index, (ident, label) in enumerate(entries):
                if index == 2:
                    u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                u.AppendMenuW(menu, MF_STRING, ident, label)
            point = wt.POINT()
            if not u.GetCursorPos(ctypes.byref(point)):
                return
            u.SetForegroundWindow(hwnd)
            choice = u.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                      point.x, point.y, 0, hwnd, None)
            u.PostMessageW(hwnd, WM_NULL, 0, 0)
            action = {MENU_SHOW: "show", MENU_LIBRARY: "library", MENU_EXIT: "exit"}.get(choice)
            if action:
                self.events.put(action)
        finally:
            u.DestroyMenu(menu)
