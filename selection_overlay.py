"""Windows marquee drawn inside one fixed, mouse-transparent viewport.

Construct and call only on the owner's Tk thread. ``show`` accepts visible
canvas pixels (not canvas scroll coordinates), normalizes and clips the box,
and returns whether it is displayed. Empty/unmapped boxes hide the overlay.
Unsupported platforms and native/Tk failures return False; a failure disables
``supported`` and releases the window so the caller can use its fallback.
Malformed coordinates raise ValueError; calls from another thread raise
RuntimeError before touching Tk. hide/close are idempotent, including after
owner destruction. There are no scheduled callbacks or pointer operations.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import math
import sys
import threading
import tkinter as tk


_TRANSPARENT = '#010203'
_EX_STYLE = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080
_GWL_EXSTYLE = -20
_user32 = None


def _api():
    """Keep HWND and LONG_PTR pointer-sized on 64-bit Windows."""
    global _user32
    if _user32 is None:
        u = C.WinDLL('user32', use_last_error=True)
        u.GetAncestor.argtypes, u.GetAncestor.restype = [W.HWND, W.UINT], W.HWND
        u.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int,
                                  C.c_int, C.c_int, W.UINT]
        u.SetWindowPos.restype = W.BOOL
        get_name = 'GetWindowLongPtrW' if C.sizeof(C.c_void_p) == 8 else 'GetWindowLongW'
        set_name = 'SetWindowLongPtrW' if C.sizeof(C.c_void_p) == 8 else 'SetWindowLongW'
        u._get_style = getattr(u, get_name)
        u._get_style.argtypes, u._get_style.restype = [W.HWND, C.c_int], C.c_ssize_t
        u._set_style = getattr(u, set_name)
        u._set_style.argtypes = [W.HWND, C.c_int, C.c_ssize_t]
        u._set_style.restype = C.c_ssize_t
        _user32 = u
    return _user32


class SelectionOverlay:
    def __init__(self, owner_canvas, color):
        self.owner_canvas = owner_canvas
        self.color = color
        self.supported = sys.platform == 'win32'
        self.visible = False
        self.last_error = None
        self._thread = threading.get_ident()
        self._closed = False
        self._visibility_generation = 0
        self._window = self._canvas = self._rectangle = None
        self._geometry = self._hwnd = None
        self._bindings = []
        if self.supported:
            try:
                # Bind only on the owner, so destroying this Toplevel cannot
                # recursively close it through a toplevel bindtag.
                for event in ('<Destroy>', '<Unmap>'):
                    token = owner_canvas.bind(event, self._owner_event, add='+')
                    self._bindings.append((event, token))
            except tk.TclError as error:
                self._fail(error)

    def _assert_thread(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError('SelectionOverlay must run on its Tk UI thread')

    def _owner_event(self, event):
        if event.widget is self.owner_canvas:
            if event.type == tk.EventType.Destroy:
                self.close()
            else:
                self.hide()

    def _styles(self):
        u = _api()
        # winfo_id is Tk's client HWND; native styles belong to its wrapper.
        hwnd = u.GetAncestor(self._window.winfo_id(), 2)  # GA_ROOT
        if not hwnd:
            raise OSError('Selection overlay wrapper HWND is unavailable')
        C.set_last_error(0)
        style = u._get_style(hwnd, _GWL_EXSTYLE)
        if not style and C.get_last_error():
            raise C.WinError(C.get_last_error())
        C.set_last_error(0)
        previous = u._set_style(hwnd, _GWL_EXSTYLE, style | _EX_STYLE)
        if not previous and C.get_last_error():
            raise C.WinError(C.get_last_error())
        if u._get_style(hwnd, _GWL_EXSTYLE) & _EX_STYLE != _EX_STYLE:
            raise OSError('Selection overlay transparent/no-activate styles were not applied')
        self._hwnd = hwnd
        # Refresh the native frame without moving/resizing/activating it.
        if not u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037):
            raise C.WinError(C.get_last_error())

    def _can_show(self, generation, window=None):
        """Reject work invalidated by callbacks processed during a Tk flush."""
        if self._closed or not self.supported or generation != self._visibility_generation:
            return False
        if not self.owner_canvas.winfo_exists():
            self.close()
            return False
        if window is not None and (self._window is not window or not window.winfo_exists()):
            self.close()
            return False
        return bool(self.owner_canvas.winfo_ismapped())

    def _create(self, generation):
        owner = self.owner_canvas.winfo_toplevel()
        window = tk.Toplevel(owner, takefocus=False)
        self._window = window
        window.withdraw()
        window.overrideredirect(True)
        window.transient(owner)
        window.configure(background=_TRANSPARENT)
        # Avoid an outline disappearing if the caller uses our color key.
        key = '#010204' if window.winfo_rgb(self.color) == window.winfo_rgb(_TRANSPARENT) else _TRANSPARENT
        window.configure(background=key)
        window.attributes('-transparentcolor', key)
        canvas = tk.Canvas(window, background=key, borderwidth=0,
                           highlightthickness=0, takefocus=False)
        self._canvas = canvas
        canvas.pack(fill='both', expand=True)
        self._rectangle = canvas.create_rectangle(0, 0, 0, 0, outline=self.color,
                                                   width=2, fill='', state='hidden')
        window.update_idletasks()
        # update_idletasks executes application after_idle callbacks too:
        # they may hide/destroy the owner or explicitly cancel this overlay.
        if not self._can_show(generation, window):
            self.hide()
            return False
        self._styles()
        return True

    def _position(self, geometry):
        x, y, width, height = geometry
        # Native coordinates are absolute even on monitors left/above the
        # primary screen; Tk's '-x/-y' geometry syntax anchors to screen edges.
        if not _api().SetWindowPos(self._hwnd, 0, x, y, width, height, 0x0014):
            raise C.WinError(C.get_last_error())

    def show(self, bounds):
        self._assert_thread()
        if self._closed or not self.supported:
            return False
        generation = self._visibility_generation
        try:
            left, top, right, bottom = (float(value) for value in bounds)
            if not all(math.isfinite(value) for value in (left, top, right, bottom)):
                raise ValueError
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError('Selection bounds must contain four finite coordinates') from error
        try:
            owner = self.owner_canvas
            if not owner.winfo_exists():
                self.close()
                return False
            if not owner.winfo_ismapped():
                self.hide()
                return False
            width, height = owner.winfo_width(), owner.winfo_height()
            left, right = sorted((left, right))
            top, bottom = sorted((top, bottom))
            # Canvas/window clipping keeps the stroke inside the viewport,
            # including selection boxes extending beyond any viewport edge.
            left, right = max(0, left), min(width, right)
            top, bottom = max(0, top), min(height, bottom)
            if right - left <= 3 or bottom - top <= 3:
                self.hide()
                return False
            if self._window is None and not self._create(generation):
                return False
            window = self._window
            if not self._can_show(generation, window):
                self.hide()
                return False
            if self._hwnd is None:
                # An earlier creation was canceled by an ordinary owner hide
                # before styles were initialized; resume only this new show.
                self._styles()
            # Idle geometry callbacks may have resized the owner's viewport.
            width, height = owner.winfo_width(), owner.winfo_height()
            right, bottom = min(width, right), min(height, bottom)
            if right - left <= 3 or bottom - top <= 3:
                self.hide()
                return False
            geometry = (owner.winfo_rootx(), owner.winfo_rooty(), width, height)
            if geometry != self._geometry:
                x, y, width, height = geometry
                self._window.geometry(f'{width}x{height}')
                self._position(geometry)
                self._geometry = geometry
            self._canvas.coords(self._rectangle, left, top, right, bottom)
            self._canvas.itemconfigure(self._rectangle, state='normal')
            if not self.visible:
                # Apply NOACTIVATE before mapping; reapply after Tk's wm map
                # in case its wrapper/style configuration changed.
                self._window.deiconify()
                self._styles()
                self._position(geometry)
                # HWND_TOP, NOMOVE|NOSIZE|NOACTIVATE|SHOWWINDOW. Owned and
                # transient, but never globally topmost or focus/grab-owning.
                if not _api().SetWindowPos(self._hwnd, 0, 0, 0, 0, 0, 0x0053):
                    raise C.WinError(C.get_last_error())
            self.visible = True
            return True
        except (tk.TclError, OSError, AttributeError) as error:
            if self._closed or generation != self._visibility_generation:
                self.hide()
                return False
            self._fail(error)
            return False

    def hide(self):
        self._assert_thread()
        self._visibility_generation += 1
        self.visible = False
        if self._window is not None:
            try:
                self._window.withdraw()
                self._canvas.itemconfigure(self._rectangle, state='hidden')
            except tk.TclError:
                # Window/root destruction may already have released Tk.
                pass

    def _dispose(self):
        owner, self.owner_canvas = self.owner_canvas, None
        window, self._window = self._window, None
        self._canvas = self._rectangle = None
        self._geometry = self._hwnd = None
        self.visible = False
        for event, token in self._bindings:
            try:
                owner.unbind(event, token)
            except tk.TclError:
                pass
        self._bindings = []
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _fail(self, error):
        self.last_error = str(error)
        self.supported = False
        self._dispose()

    def close(self):
        self._assert_thread()
        self._visibility_generation += 1
        self._closed = True
        self._dispose()
