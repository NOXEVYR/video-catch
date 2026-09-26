"""macOS capture metadata and native controls; frameworks load only on macOS.

Coordinates are Quartz global logical points (main screen top-left is 0, 0).
Retina pixel sizes and scale factors are separate. AVFoundation screen ordinals
follow CGGetActiveDisplayList, not NSScreen ordering or display IDs.
"""
from __future__ import annotations

from collections import deque
import ctypes as C
import math
import os
import sys
import uuid

_quartz = _cocoa = _carbon = None


def _frameworks():
    global _quartz, _cocoa
    if sys.platform != "darwin":
        raise OSError("此捕获后端仅支持 macOS")
    if _quartz is None:
        try:
            import Quartz
            import Cocoa
        except ImportError as exc:
            raise OSError("macOS 捕获组件缺失，请重新安装完整的拾影 macOS 应用") from exc
        _quartz, _cocoa = Quartz, Cocoa
    return _quartz, _cocoa


def enable_dpi_awareness():
    # Aqua is DPI aware. Its coordinates remain logical points on Retina displays.
    return True


def screen_capture_permission(request=False):
    q, _ = _frameworks()
    preflight = getattr(q, "CGPreflightScreenCaptureAccess", None)
    if preflight is None:  # Systems before macOS 10.15 do not implement this TCC gate.
        return True
    if preflight():
        return True
    if request:
        prompt = getattr(q, "CGRequestScreenCaptureAccess", None)
        if prompt:
            return bool(prompt())
    return False


def require_screen_capture_permission(request=False):
    if not screen_capture_permission(request=request):
        raise PermissionError("请在 macOS「系统设置 → 隐私与安全性 → 屏幕与系统音频录制」中允许拾影，随后重新打开应用")


def _rect(rect):
    return dict(x=round(rect.origin.x), y=round(rect.origin.y),
                width=round(rect.size.width), height=round(rect.size.height))


def list_monitors():
    q, _ = _frameworks()
    error, ids, count = q.CGGetActiveDisplayList(64, None, None)
    if error or not count:
        raise OSError(f"无法读取 macOS 显示器（{error}）")
    main = int(q.CGMainDisplayID())
    monitors = []
    for index, display in enumerate(ids[:count]):
        display = int(display)
        bounds = _rect(q.CGDisplayBounds(display))
        if bounds["width"] <= 0 or bounds["height"] <= 0:
            continue
        mode = q.CGDisplayCopyDisplayMode(display)
        if mode is not None:
            pixel_width = int(q.CGDisplayModeGetPixelWidth(mode))
            pixel_height = int(q.CGDisplayModeGetPixelHeight(mode))
        else:
            pixel_width, pixel_height = int(q.CGDisplayPixelsWide(display)), int(q.CGDisplayPixelsHigh(display))
        monitors.append(dict(id=display, display_id=display, display_index=index,
                             name=f"显示器 {index + 1}" + ("（主屏）" if display == main else ""),
                             primary=display == main, **bounds, pixel_width=pixel_width,
                             pixel_height=pixel_height, scale_x=pixel_width / bounds["width"],
                             scale_y=pixel_height / bounds["height"]))
    if not monitors:
        raise OSError("当前没有可用的 macOS 显示器")
    return monitors


def desktop_bounds():
    monitors = list_monitors()
    x, y = min(m["x"] for m in monitors), min(m["y"] for m in monitors)
    return dict(x=x, y=y, width=max(m["x"] + m["width"] for m in monitors) - x,
                height=max(m["y"] + m["height"] for m in monitors) - y)


def monitor_for_bounds(bounds, monitors=None):
    """AVFoundation captures one display; never silently trim a cross-screen area."""
    for monitor in monitors if monitors is not None else list_monitors():
        if (bounds["width"] > 0 and bounds["height"] > 0 and bounds["x"] >= monitor["x"]
                and bounds["y"] >= monitor["y"]
                and bounds["x"] + bounds["width"] <= monitor["x"] + monitor["width"]
                and bounds["y"] + bounds["height"] <= monitor["y"] + monitor["height"]):
            return monitor
    raise ValueError("macOS 区域／窗口录制须完全位于同一显示器内，请重新选择单屏区域")


def _window_rows():
    q, _ = _frameworks()
    return q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements,
                                       q.kCGNullWindowID) or []


def _window_info(row, monitors):
    bounds = row.get("kCGWindowBounds", {})
    result = dict(x=math.floor(float(bounds.get("X", 0))), y=math.floor(float(bounds.get("Y", 0))),
                  width=math.ceil(float(bounds.get("Width", 0))), height=math.ceil(float(bounds.get("Height", 0))))
    if result["width"] <= 0 or result["height"] <= 0:
        raise ValueError("窗口范围为空")
    result.update(hwnd=int(row["kCGWindowNumber"]), title=str(row.get("kCGWindowName") or row.get("kCGWindowOwnerName") or "未命名窗口"))
    result.update(client_x=result["x"], client_y=result["y"], client_width=result["width"], client_height=result["height"])
    try:
        monitor = monitor_for_bounds(result, monitors)
    except ValueError:
        # Preserve the visible choice; the engine rejects it with a precise error.
        result.update(display_id=None, display_index=None, spans_displays=True)
    else:
        result.update(display_id=monitor["id"], display_index=monitor["display_index"], spans_displays=False,
                      screen_x=monitor["x"], screen_y=monitor["y"], screen_width=monitor["width"],
                      screen_height=monitor["height"], pixel_width=monitor["pixel_width"],
                      pixel_height=monitor["pixel_height"], scale_x=monitor["scale_x"], scale_y=monitor["scale_y"])
    return result


def list_windows(exclude_own_process=True):
    monitors = list_monitors()
    result = []
    for row in _window_rows():
        if int(row.get("kCGWindowLayer", 0)) != 0 or not row.get("kCGWindowIsOnscreen", True):
            continue
        if exclude_own_process and int(row.get("kCGWindowOwnerPID", -1)) == os.getpid():
            continue
        if float(row.get("kCGWindowAlpha", 1)) <= 0:
            continue
        try:
            result.append(_window_info(row, monitors))
        except (ValueError, TypeError, KeyError):
            continue
    return result


def window_info(hwnd):
    try:
        hwnd = int(hwnd)
    except (ValueError, TypeError) as exc:
        raise ValueError("窗口标识无效") from exc
    for row in _window_rows():
        if int(row.get("kCGWindowNumber", -1)) == hwnd:
            return _window_info(row, list_monitors())
    raise ValueError("目标窗口已关闭、隐藏或最小化，请重新选择")


def _nswindow(window):
    """Resolve only this process's window using a temporary unique title.

    Tk's winfo_id is a drawable on Aqua, not an NSWindow pointer. Avoid casting
    it or depending on private Tk structure layouts. The title is restored in
    the same UI turn and the resulting Cocoa object is cached on that Tk object.
    """
    _, cocoa = _frameworks()
    cached = getattr(window, "_capture_nswindow", None)
    if cached is not None:
        return cached
    marker = "VideoCatchNative_" + uuid.uuid4().hex
    original = window.title()
    try:
        window.title(marker)
        window.update_idletasks()
        for native in cocoa.NSApplication.sharedApplication().windows():
            if str(native.title()) == marker:
                window._capture_nswindow = native
                return native
    finally:
        window.title(original)
    raise OSError("无法定位拾影的 macOS 原生窗口")


def tk_window_id(window):
    return int(_nswindow(window).windowNumber())


def place_window(window, bounds):
    q, cocoa = _frameworks()
    window.geometry(f"{int(bounds['width'])}x{int(bounds['height'])}+0+0")
    window.update_idletasks()
    native = _nswindow(window)
    primary_height = float(q.CGDisplayBounds(q.CGMainDisplayID()).size.height)
    frame = cocoa.NSMakeRect(float(bounds["x"]), primary_height - bounds["y"] - bounds["height"],
                             float(bounds["width"]), float(bounds["height"]))
    native.setFrame_display_(frame, True)
    # Independent per-screen overlay windows can join the active Spaces.
    behavior = native.collectionBehavior()
    native.setCollectionBehavior_(behavior | cocoa.NSWindowCollectionBehaviorCanJoinAllSpaces
                                   | cocoa.NSWindowCollectionBehaviorFullScreenAuxiliary)


def exclude_from_capture(window):
    try:
        native = _nswindow(window)
        native.setSharingType_(0)
    except (OSError, AttributeError, RuntimeError):
        return False
    # NSWindow sharingType is legacy and ignored by modern screen capture.
    # Best effort is applied, but callers must not promise toolbar exclusion.
    return False


def set_click_through(window, enabled=True):
    _nswindow(window).setIgnoresMouseEvents_(bool(enabled))


class EventTypeSpec(C.Structure):
    _fields_ = [("eventClass", C.c_uint32), ("eventKind", C.c_uint32)]


class EventHotKeyID(C.Structure):
    _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]


EVENT_HANDLER = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)


def _fourcc(value):
    return int.from_bytes(value.encode("ascii"), "big")


def _carbon_api():
    global _carbon
    if sys.platform != "darwin":
        raise OSError("macOS 全局快捷键不可用于当前系统")
    if _carbon is None:
        carbon = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        specs = {
            "GetApplicationEventTarget": ([], C.c_void_p),
            "GetEventKind": ([C.c_void_p], C.c_uint32),
            "InstallEventHandler": ([C.c_void_p, EVENT_HANDLER, C.c_uint32, C.POINTER(EventTypeSpec),
                                      C.c_void_p, C.POINTER(C.c_void_p)], C.c_int32),
            "RemoveEventHandler": ([C.c_void_p], C.c_int32),
            "RegisterEventHotKey": ([C.c_uint32, C.c_uint32, EventHotKeyID, C.c_void_p,
                                     C.c_uint32, C.POINTER(C.c_void_p)], C.c_int32),
            "UnregisterEventHotKey": ([C.c_void_p], C.c_int32),
            "GetEventParameter": ([C.c_void_p, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32),
                                    C.c_uint32, C.POINTER(C.c_uint32), C.c_void_p], C.c_int32),
        }
        for name, (args, result) in specs.items():
            getattr(carbon, name).argtypes, getattr(carbon, name).restype = args, result
        _carbon = carbon
    return _carbon


class HotkeyManager:
    """Carbon registers only four requested chords; callbacks run on Tk after().

    Registration conflicts are actual OSStatus results. No key event tap, input
    monitoring permission, key synthesis, or broad keystroke observation is used.
    """
    KEYS = {"start_stop": (1, 101, "Ctrl+Option+F9"),
            "pause_resume": (2, 109, "Ctrl+Option+F10"),
            "screenshot": (3, 103, "Ctrl+Option+F11"),
            "annotate": (4, 111, "Ctrl+Option+F12")}

    def __init__(self, root, callbacks):
        self.root, self.callbacks = root, dict(callbacks)
        self.status = {}
        self._owned, self._actions, self._pending = {}, {}, deque()
        self._pressed = set()
        self._signature = uuid.uuid4().int & 0xffffffff
        self._handler = C.c_void_p()
        self._proc = self._timer = None
        self._running = False

    def start(self):
        if self._running:
            return dict(self.status)
        self.status = {}
        try:
            carbon = _carbon_api()
            target = carbon.GetApplicationEventTarget()
            @EVENT_HANDLER
            def handler(_next, event, _data):
                hotkey = EventHotKeyID()
                status = carbon.GetEventParameter(event, _fourcc("----"), _fourcc("hkid"), None,
                                                   C.sizeof(hotkey), None, C.byref(hotkey))
                if status == 0 and hotkey.signature == self._signature and hotkey.id in self._actions:
                    if carbon.GetEventKind(event) == 6:  # kEventHotKeyReleased
                        self._pressed.discard(hotkey.id)
                    elif hotkey.id not in self._pressed:
                        self._pressed.add(hotkey.id)
                        self._pending.append(self._actions[hotkey.id])
                    return 0
                return -9874  # eventNotHandledErr: leave other handlers untouched.
            self._proc = handler
            event_types = (EventTypeSpec * 2)(EventTypeSpec(_fourcc("keyb"), 5), EventTypeSpec(_fourcc("keyb"), 6))
            error = carbon.InstallEventHandler(target, handler, 2, event_types, None, C.byref(self._handler))
            if error:
                raise OSError(f"无法安装 macOS 快捷键回调（{error}）")
            for action, (ident, key, label) in self.KEYS.items():
                if action not in self.callbacks:
                    continue
                ref = C.c_void_p()
                error = carbon.RegisterEventHotKey(key, 0x1800, EventHotKeyID(self._signature, ident),
                                                   target, 0, C.byref(ref))
                registered = error == 0 and bool(ref.value)
                self.status[action] = dict(registered=registered, hotkey=label,
                                           error="" if registered else f"快捷键被占用或系统不支持（{error}）")
                if registered:
                    self._owned[ident], self._actions[ident] = ref, action
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
        if self._owned or self._handler.value:
            carbon = _carbon_api()
            for ref in self._owned.values():
                carbon.UnregisterEventHotKey(ref)
            self._owned.clear()
            self._actions.clear()
            if self._handler.value:
                carbon.RemoveEventHandler(self._handler)
                self._handler = C.c_void_p()
        self._proc = None
        self._pending.clear()
        self._pressed.clear()
