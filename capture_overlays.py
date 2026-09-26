"""Tk region selection and desktop ink. Invoke only from the Tk UI thread.

Desktop ink is captured by desktop/region recording; selected-window and camera
recording bypass desktop composition and cannot include these annotations.
"""
from __future__ import annotations

import tkinter as tk
import sys
from tkinter import ttk

from capture_native import (desktop_bounds, list_monitors, exclude_from_capture,
                            place_window, set_click_through)


def region_from_points(start, end, bounds):
    """Clamp absolute pixel coordinates; never infer Tk negative geometry offsets."""
    left, top = bounds["x"], bounds["y"]
    right, bottom = left + bounds["width"], top + bounds["height"]
    x1, x2 = sorted((max(left, min(right, int(start[0]))), max(left, min(right, int(end[0])))))
    y1, y2 = sorted((max(top, min(bottom, int(start[1]))), max(top, min(bottom, int(end[1])))))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return dict(x=x1, y=y1, width=x2 - x1, height=y2 - y1)


class RegionSelector:
    def __init__(self, root, on_selected):
        self.root, self.on_selected = root, on_selected
        self.bounds = desktop_bounds()
        self.active = True
        self._start = None
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", .32)
        self.window.configure(bg="#122033")
        self.canvas = tk.Canvas(self.window, bg="#122033", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self._box = self.canvas.create_rectangle(0, 0, 0, 0, outline="#60e7cf", width=3)
        self._label = self.canvas.create_text(30, 28, anchor="nw", fill="white",
                                             font=("Microsoft YaHei UI", 16, "bold"),
                                             text="拖动选择录制区域 · Esc 取消")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.deiconify()
        place_window(self.window, self.bounds)
        self.excluded = exclude_from_capture(self.window)
        self.window.grab_set()
        self.window.focus_force()

    def _point(self, event):
        return event.x_root, event.y_root

    def _press(self, event):
        self._start = self._point(event)
        self._drag(event)

    def _drag(self, event):
        if self._start is None:
            return
        x, y = self.bounds["x"], self.bounds["y"]
        self.canvas.coords(self._box, self._start[0] - x, self._start[1] - y,
                           event.x_root - x, event.y_root - y)
        area = region_from_points(self._start, self._point(event), self.bounds)
        if area:
            lx = min(max(12, area["x"] - x), self.bounds["width"] - 300)
            ly = min(max(12, area["y"] - y - 36), self.bounds["height"] - 48)
            self.canvas.coords(self._label, lx, ly)
            self.canvas.itemconfigure(self._label, text=f"{area['width']} × {area['height']} · 松开完成 · Esc 取消")

    def _release(self, event):
        if self._start is not None:
            self._finish(region_from_points(self._start, self._point(event), self.bounds))

    def _finish(self, result):
        if not self.active:
            return
        self.active = False
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()
        # Permit DWM to remove the selector before a callback starts capture.
        callback, self.on_selected = self.on_selected, None
        if callback:
            self.root.after(100, lambda: callback(result))

    def close(self):
        self._finish(None)


def select_region(root, on_selected):
    return (MacRegionSelector if sys.platform == "darwin" else RegionSelector)(root, on_selected)


class AnnotationOverlay:
    """Color-key ink plus a nearly transparent input layer and excluded toolbar.

    Finish drawing removes the input layer, leaving ink visible and the desktop
    usable. toggle() hides all ink/controls or opens a fresh drawing session.
    """
    KEY_COLOR = "#010203"

    def __new__(cls, root, on_input_ready=None):
        if cls is AnnotationOverlay and sys.platform == "darwin":
            return object.__new__(MacAnnotationOverlay)
        return object.__new__(cls)

    def __init__(self, root, on_input_ready=None):
        self.root = root
        self.on_input_ready = on_input_ready
        self.active = False
        self.drawing = False
        self.window = self.canvas = self.toolbar = self.input_window = None
        self.toolbar_excluded = False
        self.color = "#ff4e64"
        self.width = 5
        self._strokes = []
        self._current = None
        self._last = None
        self._drawing_button = None

    def toggle(self):
        if self.active:
            self.close()
        else:
            self._open()
        return self.active

    def _open(self):
        self.bounds = desktop_bounds()
        self._open_ink()
        self._open_toolbar()

    def _open_ink(self):
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=self.KEY_COLOR)
        self.window.attributes("-transparentcolor", self.KEY_COLOR)
        self.window.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.window, bg=self.KEY_COLOR, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.window.deiconify()
        place_window(self.window, self.bounds)
        set_click_through(self.window)
        # Do NOT exclude the ink window: those pixels are intended for recordings.

    def _open_toolbar(self):
        self.toolbar = tk.Toplevel(self.root)
        self.toolbar.withdraw()
        self.toolbar.overrideredirect(True)
        self.toolbar.attributes("-topmost", True)
        self.toolbar.configure(bg="#15202f")
        frame = ttk.Frame(self.toolbar, padding=7)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="桌面画笔").pack(side="left", padx=(0, 8))
        for color in ("#ff4e64", "#ffd247", "#57e7af", "#64adff", "#ffffff"):
            tk.Button(frame, bg=color, activebackground=color, width=2, relief="flat",
                      command=lambda value=color: self.set_color(value)).pack(side="left", padx=2)
        self._width_var = tk.StringVar(value=str(self.width))
        width_control = ttk.Combobox(frame, textvariable=self._width_var, state="readonly", width=2,
                                     values=(2, 5, 9, 14))
        width_control.pack(side="left", padx=5)
        width_control.bind("<<ComboboxSelected>>", lambda _event: self.set_width(self._width_var.get()))
        ttk.Button(frame, text="撤销", command=self.undo, width=5).pack(side="left", padx=2)
        ttk.Button(frame, text="清空", command=self.clear, width=5).pack(side="left", padx=2)
        self._drawing_button = ttk.Button(frame, text="结束绘制", command=self.toggle_drawing, width=9)
        self._drawing_button.pack(side="left", padx=3)
        ttk.Button(frame, text="退出标注", command=self.close, width=8).pack(side="left", padx=2)
        self.toolbar.deiconify()
        self.toolbar.update_idletasks()
        primary = next((item for item in list_monitors() if item["primary"]), self.bounds)
        width = self.toolbar.winfo_reqwidth()
        place_window(self.toolbar, dict(x=primary["x"] + max(0, (primary["width"] - width) // 2),
                                        y=primary["y"] + 90, width=width,
                                        height=self.toolbar.winfo_reqheight()))
        self.toolbar_excluded = exclude_from_capture(self.toolbar)
        self.active = True
        self._start_drawing()

    def set_color(self, color):
        self.color = color

    def set_width(self, width):
        self.width = max(1, min(32, int(width)))

    def _start_drawing(self):
        if not self.active or self.drawing:
            return
        self._open_input()
        self._raise_ink()
        self.toolbar.lift()
        self.input_window.focus_force()
        self.drawing = True
        self._drawing_button.configure(text="结束绘制")
        # Let recording transport controls remain clickable above this layer.
        if self.on_input_ready:
            self.on_input_ready()

    def _open_input(self):
        # A color-key window passes background clicks through. A separate almost
        # invisible input layer captures drawing without fading the ink itself.
        self.input_window = tk.Toplevel(self.root)
        self.input_window.withdraw()
        self.input_window.overrideredirect(True)
        self.input_window.attributes("-topmost", True)
        self.input_window.attributes("-alpha", .015)
        self.input_window.configure(bg="black", cursor="crosshair")
        self.input_window.bind("<ButtonPress-1>", self._press)
        self.input_window.bind("<B1-Motion>", self._drag)
        self.input_window.bind("<ButtonRelease-1>", self._release)
        self.input_window.bind("<Escape>", lambda _event: self.finish())
        self.input_window.bind("<Control-z>", lambda _event: self.undo())
        self.input_window.deiconify()
        place_window(self.input_window, self.bounds)
        exclude_from_capture(self.input_window)

    def _raise_ink(self):
        self.window.lift()

    def toggle_drawing(self):
        if self.drawing:
            self.finish()
        else:
            self._start_drawing()

    def finish(self):
        self._release(None)
        if self.input_window:
            self.input_window.destroy()
            self.input_window = None
        self.drawing = False
        if self._drawing_button:
            self._drawing_button.configure(text="继续绘制")

    def _point(self, event):
        return event.x_root - self.bounds["x"], event.y_root - self.bounds["y"]

    def _press(self, event):
        self._last = self._point(event)
        x, y = self._last
        radius = self.width / 2
        self._current = [self.canvas.create_oval(x-radius, y-radius, x+radius, y+radius,
                                                fill=self.color, outline=self.color)]
        self._strokes.append(self._current)

    def _drag(self, event):
        if self._current is None:
            return
        point = self._point(event)
        self._current.append(self.canvas.create_line(*self._last, *point, fill=self.color,
                                                      width=self.width, capstyle="round", joinstyle="round"))
        self._last = point

    def _release(self, _event):
        self._current = self._last = None

    def undo(self):
        self._release(None)
        if self._strokes and self.canvas:
            for item in self._strokes.pop():
                self.canvas.delete(item)

    def clear(self):
        self._release(None)
        if self.canvas:
            self.canvas.delete("all")
        self._strokes.clear()

    def close(self):
        self.active = self.drawing = False
        self._current = self._last = None
        for window in (self.input_window, self.toolbar, self.window):
            if window:
                try:
                    window.destroy()
                except tk.TclError:
                    pass
        self.input_window = self.toolbar = self.window = self.canvas = self._drawing_button = None
        self._strokes.clear()


class MacRegionSelector(RegionSelector):
    """One Aqua surface per display, including screens in separate Spaces."""
    def __init__(self, root, on_selected):
        self.root, self.on_selected = root, on_selected
        self.bounds = desktop_bounds()
        self.active, self._start = True, None
        self._surfaces = []
        self.excluded = False
        try:
            for bounds in list_monitors():
                window = tk.Toplevel(root)
                window.withdraw()
                window.overrideredirect(True)
                window.attributes("-topmost", True)
                window.attributes("-alpha", .32)
                canvas = tk.Canvas(window, bg="#122033", highlightthickness=0, cursor="crosshair")
                canvas.pack(fill="both", expand=True)
                box = canvas.create_rectangle(0, 0, 0, 0, outline="#60e7cf", width=3)
                label = canvas.create_text(30, 40, anchor="nw", fill="white", font=("Helvetica", 16, "bold"),
                                           text="拖动选择录制区域 · Esc 取消 · 单次录制限一个显示器")
                self._surfaces.append((window, canvas, bounds, box, label))
                canvas.bind("<ButtonPress-1>", self._press)
                canvas.bind("<B1-Motion>", self._drag)
                canvas.bind("<ButtonRelease-1>", self._release)
                window.bind("<Escape>", lambda _event: self.close())
                window.protocol("WM_DELETE_WINDOW", self.close)
                window.deiconify()
                place_window(window, bounds)
                exclude_from_capture(window)
            self.window, self.canvas, _, self._box, self._label = self._surfaces[0]
            self.window.focus_force()
        except Exception:
            for window, *_ in self._surfaces:
                window.destroy()
            self.active = False
            raise

    def _press(self, event):
        # Grab the monitor actually clicked, rather than pinning input to screen 0.
        self.window = event.widget.winfo_toplevel()
        self.window.grab_set()
        self.window.focus_force()
        super()._press(event)

    def _drag(self, event):
        if self._start is None:
            return
        area = region_from_points(self._start, self._point(event), self.bounds)
        for _window, canvas, bounds, box, label in self._surfaces:
            x, y = bounds["x"], bounds["y"]
            canvas.coords(box, self._start[0] - x, self._start[1] - y, event.x_root - x, event.y_root - y)
            if area:
                canvas.itemconfigure(label, text=f"{area['width']} × {area['height']} 点 · 松开完成 · Esc 取消")

    def _finish(self, result):
        if not self.active:
            return
        for window, *_ in self._surfaces:
            if window is not self.window:
                window.destroy()
        self._surfaces.clear()
        super()._finish(result)


class _DesktopCanvas:
    """Fan out logical-desktop strokes to per-screen canvases; undo stays atomic."""
    def __init__(self, surfaces, desktop):
        self.surfaces, self.desktop = surfaces, desktop
        self.items = {}
        self.serial = 0

    def _create(self, method, coords, options):
        refs = []
        for _window, canvas, bounds in self.surfaces:
            dx, dy = self.desktop["x"] - bounds["x"], self.desktop["y"] - bounds["y"]
            local = [value + (dy if index % 2 else dx) for index, value in enumerate(coords)]
            refs.append((canvas, getattr(canvas, method)(*local, **options)))
        self.serial += 1
        self.items[self.serial] = refs
        return self.serial

    def create_oval(self, *coords, **options):
        return self._create("create_oval", coords, options)

    def create_line(self, *coords, **options):
        return self._create("create_line", coords, options)

    def delete(self, item):
        if item == "all":
            for _window, canvas, _bounds in self.surfaces:
                canvas.delete("all")
            self.items.clear()
        else:
            for canvas, ident in self.items.pop(item, []):
                canvas.delete(ident)


class MacAnnotationOverlay(AnnotationOverlay):
    def _open_ink(self):
        self._ink_surfaces, self._inputs = [], []
        try:
            for bounds in list_monitors():
                window = tk.Toplevel(self.root)
                window.withdraw()
                window.overrideredirect(True)
                window.attributes("-transparent", True)
                window.attributes("-topmost", True)
                window.configure(bg="systemTransparent")
                canvas = tk.Canvas(window, bg="systemTransparent", highlightthickness=0)
                canvas.pack(fill="both", expand=True)
                self._ink_surfaces.append((window, canvas, bounds))
                window.deiconify()
                place_window(window, bounds)
                set_click_through(window)
            self.window = self._ink_surfaces[0][0]
            self.canvas = _DesktopCanvas(self._ink_surfaces, self.bounds)
        except Exception:
            self.close()
            raise

    def _open_input(self):
        self._inputs = []
        try:
            for _ink, _canvas, bounds in self._ink_surfaces:
                window = tk.Toplevel(self.root)
                self._inputs.append(window)
                window.withdraw()
                window.overrideredirect(True)
                window.attributes("-topmost", True)
                window.attributes("-alpha", .015)
                window.configure(bg="black", cursor="crosshair")
                window.bind("<ButtonPress-1>", self._press)
                window.bind("<B1-Motion>", self._drag)
                window.bind("<ButtonRelease-1>", self._release)
                window.bind("<Escape>", lambda _event: self.finish())
                window.bind("<Command-z>", lambda _event: self.undo())
                window.bind("<Control-z>", lambda _event: self.undo())
                window.deiconify()
                place_window(window, bounds)
                exclude_from_capture(window)
            self.input_window = self._inputs[0]
        except Exception:
            self.finish()
            raise

    def _raise_ink(self):
        for window, *_ in self._ink_surfaces:
            window.lift()

    def finish(self):
        for window in getattr(self, "_inputs", []):
            if window is not self.input_window:
                window.destroy()
        self._inputs = []
        super().finish()

    def close(self):
        for window in getattr(self, "_inputs", []):
            if window is not self.input_window:
                window.destroy()
        for window, *_ in getattr(self, "_ink_surfaces", []):
            if window is not self.window:
                window.destroy()
        self._inputs, self._ink_surfaces = [], []
        super().close()
