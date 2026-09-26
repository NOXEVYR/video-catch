"""Small, pointer-safe Windows capture helpers. All Tk methods run on its thread."""
from __future__ import annotations

from collections import deque
import ctypes as C
from ctypes import wintypes as W
import os
import sys
import uuid


_user32 = _kernel32 = _dwmapi = None
WNDPROC = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM) if os.name == "nt" else None
MONPROC = C.WINFUNCTYPE(W.BOOL, W.HANDLE, W.HDC, C.POINTER(W.RECT), W.LPARAM) if os.name == "nt" else None
ENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM) if os.name == "nt" else None


class MONITORINFOEX(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT),
                ("dwFlags", W.DWORD), ("szDevice", W.WCHAR * 32)]


class WNDCLASS(C.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC or C.c_void_p),
                ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int), ("hInstance", W.HINSTANCE),
                ("hIcon", W.HICON), ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


def _api():
    global _user32, _kernel32, _dwmapi
    if os.name != "nt":
        raise OSError("屏幕录制的原生窗口功能仅支持 Windows")
    if _user32 is not None:
        return _user32
    u = C.WinDLL("user32", use_last_error=True)
    signatures = {
        "GetSystemMetrics": ([C.c_int], C.c_int),
        "EnumDisplayMonitors": ([W.HDC, C.POINTER(W.RECT), MONPROC, W.LPARAM], W.BOOL),
        "GetMonitorInfoW": ([W.HANDLE, C.POINTER(MONITORINFOEX)], W.BOOL),
        "EnumWindows": ([ENUMPROC, W.LPARAM], W.BOOL),
        "IsWindow": ([W.HWND], W.BOOL), "IsWindowVisible": ([W.HWND], W.BOOL),
        "IsIconic": ([W.HWND], W.BOOL),
        "GetWindowRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
        "GetClientRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
        "ClientToScreen": ([W.HWND, C.POINTER(W.POINT)], W.BOOL),
        "GetWindowTextLengthW": ([W.HWND], C.c_int),
        "GetWindowTextW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
        "GetWindowThreadProcessId": ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
        "GetAncestor": ([W.HWND, W.UINT], W.HWND),
        "SetWindowDisplayAffinity": ([W.HWND, W.DWORD], W.BOOL),
        "SetWindowPos": ([W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT], W.BOOL),
        "RegisterHotKey": ([W.HWND, C.c_int, W.UINT, W.UINT], W.BOOL),
        "UnregisterHotKey": ([W.HWND, C.c_int], W.BOOL),
        "PeekMessageW": ([C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT], W.BOOL),
        "DispatchMessageW": ([C.POINTER(W.MSG)], C.c_ssize_t),
        "DefWindowProcW": ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], C.c_ssize_t),
        "RegisterClassW": ([C.POINTER(WNDCLASS)], W.WORD),
        "UnregisterClassW": ([W.LPCWSTR, W.HINSTANCE], W.BOOL),
        "CreateWindowExW": ([W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int,
                             C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID], W.HWND),
        "DestroyWindow": ([W.HWND], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(u, name)
        fn.argtypes, fn.restype = args, result
    get_name = "GetWindowLongPtrW" if C.sizeof(C.c_void_p) == 8 else "GetWindowLongW"
    set_name = "SetWindowLongPtrW" if C.sizeof(C.c_void_p) == 8 else "SetWindowLongW"
    u._get_style = getattr(u, get_name)
    u._get_style.argtypes, u._get_style.restype = [W.HWND, C.c_int], C.c_ssize_t
    u._set_style = getattr(u, set_name)
    u._set_style.argtypes, u._set_style.restype = [W.HWND, C.c_int, C.c_ssize_t], C.c_ssize_t
    k = C.WinDLL("kernel32", use_last_error=True)
    k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [W.LPCWSTR], W.HMODULE
    _kernel32 = k
    try:
        _dwmapi = C.WinDLL("dwmapi", use_last_error=True)
        _dwmapi.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD, W.LPVOID, W.DWORD]
        _dwmapi.DwmGetWindowAttribute.restype = C.c_long
    except OSError:
        _dwmapi = None
    _user32 = u
    return u


def enable_dpi_awareness():
    """Call before creating any Tk window; prefer physical per-monitor coordinates."""
    if sys.platform == "darwin":
        from capture_macos import enable_dpi_awareness as mac_enable
        return mac_enable()
    if os.name != "nt":
        return False
    try:
        u = _api()
        fn = u.SetProcessDpiAwarenessContext
        fn.argtypes, fn.restype = [W.HANDLE], W.BOOL
        if fn(W.HANDLE(-4)):
            return True
    except (AttributeError, OSError):
        pass
    try:
        shcore = C.WinDLL("shcore")
        shcore.SetProcessDpiAwareness.argtypes = [C.c_int]
        shcore.SetProcessDpiAwareness.restype = C.c_long
        return shcore.SetProcessDpiAwareness(2) == 0
    except (AttributeError, OSError):
        return False


def desktop_bounds():
    if sys.platform == "darwin":
        from capture_macos import desktop_bounds as mac_bounds
        return mac_bounds()
    u = _api()
    x, y, width, height = (u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    if width <= 0 or height <= 0:
        raise OSError("无法读取桌面范围")
    return dict(x=x, y=y, width=width, height=height)


def _rect(r):
    return dict(x=r.left, y=r.top, width=r.right - r.left, height=r.bottom - r.top)


def list_monitors():
    if sys.platform == "darwin":
        from capture_macos import list_monitors as mac_monitors
        return mac_monitors()
    u = _api()
    monitors = []
    @MONPROC
    def visit(handle, _dc, _rect_ptr, _data):
        info = MONITORINFOEX()
        info.cbSize = C.sizeof(info)
        if u.GetMonitorInfoW(handle, C.byref(info)):
            monitors.append(dict(id=info.szDevice, name=info.szDevice,
                                 primary=bool(info.dwFlags & 1), **_rect(info.rcMonitor)))
        return True
    if not u.EnumDisplayMonitors(None, None, visit, 0):
        raise C.WinError(C.get_last_error())
    return sorted(monitors, key=lambda item: (not item["primary"], item["x"], item["y"]))


def window_info(hwnd):
    if sys.platform == "darwin":
        from capture_macos import window_info as mac_window
        return mac_window(hwnd)
    u = _api()
    try:
        hwnd = int(hwnd)
    except (ValueError, TypeError) as exc:
        raise ValueError("窗口句柄无效") from exc
    if hwnd <= 0 or not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
        raise ValueError("目标窗口已关闭、隐藏或最小化，请重新选择")
    r, client, point = W.RECT(), W.RECT(), W.POINT()
    if not u.GetWindowRect(hwnd, C.byref(r)) or not u.GetClientRect(hwnd, C.byref(client)):
        raise ValueError("无法获取窗口范围")
    if r.right <= r.left or r.bottom <= r.top or client.right <= 0 or client.bottom <= 0:
        raise ValueError("窗口范围为空")
    if not u.ClientToScreen(hwnd, C.byref(point)):
        raise ValueError("无法获取窗口客户区位置")
    title = C.create_unicode_buffer(u.GetWindowTextLengthW(hwnd) + 1)
    u.GetWindowTextW(hwnd, title, len(title))
    return dict(hwnd=hwnd, title=title.value, **_rect(r), client_x=point.x, client_y=point.y,
                client_width=client.right, client_height=client.bottom)


def list_windows(exclude_own_process=True):
    if sys.platform == "darwin":
        from capture_macos import list_windows as mac_windows
        return mac_windows(exclude_own_process=exclude_own_process)
    u = _api()
    windows = []
    @ENUMPROC
    def visit(hwnd, _data):
        pid = W.DWORD()
        u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if exclude_own_process and pid.value == os.getpid():
            return True
        if u._get_style(hwnd, -20) & 0x80:  # tool windows are not recording targets
            return True
        if _dwmapi:
            cloaked = W.DWORD()
            if _dwmapi.DwmGetWindowAttribute(hwnd, 14, C.byref(cloaked), C.sizeof(cloaked)) == 0 and cloaked.value:
                return True
        try:
            info = window_info(hwnd)
            if info["title"].strip():
                windows.append(info)
        except ValueError:
            pass
        return True
    if not u.EnumWindows(visit, 0):
        raise C.WinError(C.get_last_error())
    return windows


def _tk_hwnd(window):
    if sys.platform == "darwin":
        from capture_macos import tk_window_id
        return tk_window_id(window)
    window.update_idletasks()
    u = _api()
    handle = window.winfo_id()
    return u.GetAncestor(handle, 2) or handle


def exclude_from_capture(tk_window):
    """Best effort on supported Windows builds; never promises universal exclusion."""
    if sys.platform == "darwin":
        from capture_macos import exclude_from_capture as mac_exclude
        return mac_exclude(tk_window)
    try:
        return bool(_api().SetWindowDisplayAffinity(_tk_hwnd(tk_window), 0x11))
    except (OSError, AttributeError, RuntimeError):
        return False


def place_window(window, bounds):
    """Absolute virtual desktop coordinates, including negative monitor origins."""
    if sys.platform == "darwin":
        from capture_macos import place_window as mac_place
        return mac_place(window, bounds)
    window.geometry(f"{bounds['width']}x{bounds['height']}+0+0")
    window.update_idletasks()
    if os.name == "nt":
        if not _api().SetWindowPos(_tk_hwnd(window), W.HWND(-1), bounds["x"], bounds["y"],
                                  bounds["width"], bounds["height"], 0x0010):
            raise C.WinError(C.get_last_error())
    else:
        window.geometry(f"{bounds['width']}x{bounds['height']}+{bounds['x']}+{bounds['y']}")


def set_click_through(window, enabled=True):
    if sys.platform == "darwin":
        from capture_macos import set_click_through as mac_click_through
        return mac_click_through(window, enabled=enabled)
    u = _api()
    handle = _tk_hwnd(window)
    style = u._get_style(handle, -20)
    u._set_style(handle, -20, (style | 0x20 | 0x80000) if enabled else (style & ~0x20))


class HotkeyManager:
    """Owned message-only window; callback execution is deferred to Tk after().

    A window procedure collects keys even when Tk drains the native message queue
    before our timer. PeekMessage is restricted to this manager's hidden window.
    No unrelated native message or another application's hotkey is consumed.
    """
    KEYS = {"start_stop": (1, 0x78, "Ctrl+Alt+F9"),
            "pause_resume": (2, 0x79, "Ctrl+Alt+F10"),
            "screenshot": (3, 0x7A, "Ctrl+Alt+F11"),
            "annotate": (4, 0x7B, "Ctrl+Alt+F12")}

    def __new__(cls, root, callbacks):
        if sys.platform == "darwin":
            from capture_macos import HotkeyManager as MacHotkeyManager
            return MacHotkeyManager(root, callbacks)
        return super().__new__(cls)

    def __init__(self, root, callbacks):
        self.root, self.callbacks = root, dict(callbacks)
        self.status = {}
        self._owned, self._pending = {}, deque()
        self._hwnd = self._timer = self._proc = self._class = self._instance = None
        self._running = False

    def start(self):
        if self._running:
            return dict(self.status)
        self.status = {}
        try:
            u = _api()
            self._class = "VideoCatchHotkeys_" + uuid.uuid4().hex
            self._instance = _kernel32.GetModuleHandleW(None)
            @WNDPROC
            def proc(hwnd, message, wparam, lparam):
                if message == 0x0312 and int(wparam) in self._owned:
                    self._pending.append(self._owned[int(wparam)])
                    return 0
                return u.DefWindowProcW(hwnd, message, wparam, lparam)
            self._proc = proc  # Native code must retain a live Python callback.
            wc = WNDCLASS()
            wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = proc, self._instance, self._class
            if not u.RegisterClassW(C.byref(wc)):
                raise C.WinError(C.get_last_error())
            self._hwnd = u.CreateWindowExW(0, self._class, "", 0, 0, 0, 0, 0,
                                           W.HWND(-3), None, self._instance, None)
            if not self._hwnd:
                raise C.WinError(C.get_last_error())
            for action, (ident, key, label) in self.KEYS.items():
                if action not in self.callbacks:
                    continue
                registered = bool(u.RegisterHotKey(self._hwnd, ident, 0x4003, key))
                self.status[action] = dict(registered=registered, hotkey=label,
                                           error="" if registered else f"快捷键被占用或不可用（{C.get_last_error()}）")
                if registered:
                    self._owned[ident] = action
            self._running = True
            self._timer = self.root.after(60, self._poll)
        except (OSError, AttributeError) as exc:
            self.close()
            for action in self.callbacks:
                self.status[action] = dict(registered=False, hotkey=self.KEYS.get(action, (0, 0, ""))[2], error=str(exc))
        return dict(self.status)

    def _poll(self):
        self._timer = None
        if not self._running:
            return
        u, message = _api(), W.MSG()
        while u.PeekMessageW(C.byref(message), self._hwnd, 0x0312, 0x0312, 1):
            u.DispatchMessageW(C.byref(message))
        try:
            while self._pending and self._running:
                callback = self.callbacks.get(self._pending.popleft())
                if callback:
                    try:
                        callback()
                    except Exception as exc:
                        self.root.report_callback_exception(type(exc), exc, exc.__traceback__)
        finally:
            if self._running:
                self._timer = self.root.after(60, self._poll)

    def close(self):
        self._running = False
        if self._timer is not None:
            try:
                self.root.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        if self._hwnd:
            u = _api()
            for ident in self._owned:
                u.UnregisterHotKey(self._hwnd, ident)
            self._owned.clear()
            u.DestroyWindow(self._hwnd)
            self._hwnd = None
        if self._class and _user32:
            _user32.UnregisterClassW(self._class, self._instance)
        self._class = self._proc = None
        self._pending.clear()
