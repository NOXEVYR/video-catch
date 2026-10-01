"""Shared presentation and lifecycle for ordinary workbench windows."""
import tkinter as tk
from tkinter import ttk
import sys

from ui import BG, PANEL, FIELD, FG, MUTED, ACCENT, LINE


def register_styles(root):
    style = ttk.Style(root)
    style.configure('Surface.TFrame', background=PANEL)
    style.configure('Surface.TLabel', background=PANEL, foreground=FG)
    style.configure('SurfaceMuted.TLabel', background=PANEL, foreground=MUTED)
    style.configure('Title.TLabel', background=BG, foreground=FG,
                    font=('Microsoft YaHei UI', 20, 'bold'))
    style.configure('Section.TLabel', background=PANEL, foreground=FG,
                    font=('Microsoft YaHei UI', 11, 'bold'))
    style.configure('Status.TLabel', foreground=ACCENT)
    style.configure('SurfaceStatus.TLabel', background=PANEL, foreground=ACCENT)
    style.configure('Quiet.TButton', background=FIELD, foreground=FG,
                    padding=(14, 9), borderwidth=0)
    style.map('Quiet.TButton', background=[('active', '#493b57'), ('disabled', PANEL)],
              foreground=[('disabled', MUTED)])
    style.configure('Danger.TButton', background='#604250', foreground=FG)
    style.map('Danger.TButton', background=[('active', '#7b4c5b')])
    style.configure('Surface.TCheckbutton', background=PANEL, foreground=FG,
                    padding=5, focuscolor=ACCENT)
    style.map('Surface.TCheckbutton', background=[('active', PANEL)],
              indicatorbackground=[('selected', ACCENT), ('!selected', FIELD)])
    style.configure('TLabelframe', background=BG, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE)
    style.configure('TLabelframe.Label', background=BG, foreground=MUTED)
    style.configure('TMenubutton', background=FIELD, foreground=FG, padding=(12, 9),
                    borderwidth=0, arrowcolor=MUTED)
    for name, value in (('background', FIELD), ('foreground', FG),
                        ('activeBackground', '#51405d'), ('activeForeground', FG),
                        ('selectColor', ACCENT), ('borderWidth', 0),
                        ('font', ('Microsoft YaHei UI', 9))):
        root.option_add('*Menu.' + name, value)


def owner_work_area(owner):
    """Use the owner's monitor; preserve deliberately off-desktop fixtures."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
        try:
            user = ctypes.WinDLL('user32', use_last_error=True)
            left, top = user.GetSystemMetrics(76), user.GetSystemMetrics(77)
            right, bottom = left + user.GetSystemMetrics(78), top + user.GetSystemMetrics(79)
            x, y = owner.winfo_rootx(), owner.winfo_rooty()
            if x >= right or y >= bottom or x + owner.winfo_width() <= left or y + owner.winfo_height() <= top:
                return None
            user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user.GetAncestor.restype = wintypes.HWND
            user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user.MonitorFromWindow.restype = wintypes.HANDLE
            user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
            user.GetMonitorInfoW.restype = wintypes.BOOL
            handle = user.MonitorFromWindow(user.GetAncestor(owner.winfo_id(), 2), 2)
            info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
            if user.GetMonitorInfoW(handle, ctypes.byref(info)):
                return info.work.left, info.work.top, info.work.right, info.work.bottom
        except (OSError, tk.TclError):
            pass
    x, y = owner.winfo_rootx(), owner.winfo_rooty()
    if x < 0 or y < 0 or x >= owner.winfo_screenwidth() or y >= owner.winfo_screenheight():
        return None
    return 0, 0, owner.winfo_screenwidth(), owner.winfo_screenheight() - 40


def setup_window(window, app, title, width, height, *, minwidth=480, minheight=360):
    owner = getattr(app, 'root', None) if app is not None else None
    if owner is None:
        owner = window.master.winfo_toplevel()
    window.configure(bg=BG)
    window.title('拾影 · ' + title if not title.startswith('拾影') else title)
    if owner.winfo_viewable():
        window.transient(owner)
    brand = getattr(app, 'brand_image', None) if app is not None else None
    if brand is not None:
        window.iconphoto(False, brand)
    scale = max(1.0, window.winfo_fpixels('1i') / 96)
    bounds = owner_work_area(owner)
    available_w = max(320, window.winfo_screenwidth() - 48)
    available_h = max(240, window.winfo_screenheight() - 80)
    if bounds is not None:
        left, top, right, bottom = bounds
        available_w = min(available_w, max(320, right - left - 24))
        available_h = min(available_h, max(240, bottom - top - 40))
    width = min(round(width * scale), available_w)
    height = min(round(height * scale), available_h)
    window.minsize(min(round(minwidth * scale), available_w),
                   min(round(minheight * scale), available_h))
    x = owner.winfo_rootx() + max(0, (owner.winfo_width() - width) // 2)
    y = owner.winfo_rooty() + max(0, (owner.winfo_height() - height) // 2)
    if bounds is not None:
        x = max(left + 12, min(x, right - width - 12))
        y = max(top + 12, min(y, bottom - height - 40))
    # Signed Tk geometry offsets are relative to screen edges, so negative
    # absolute monitor coordinates must use an explicit '+-N' offset.
    x_text, y_text = '+' + str(x), '+' + str(y)
    window.geometry(f'{width}x{height}{x_text}{y_text}')


def header(parent, title, subtitle, app=None):
    frame = ttk.Frame(parent, padding=(20, 18, 20, 16))
    frame.pack(fill='x')
    brand = getattr(app, 'brand_image', None) if app is not None else None
    if brand is not None:
        frame.brand = brand.subsample(max(1, brand.width() // 44))
        ttk.Label(frame, image=frame.brand).pack(side='left', padx=(0, 14))
    text = ttk.Frame(frame)
    text.pack(side='left', fill='x', expand=True)
    title_label = ttk.Label(text, text=title, style='Title.TLabel', wraplength=500, justify='left')
    title_label.pack(anchor='w', fill='x')
    description = ttk.Label(text, text=subtitle, style='Muted.TLabel',
                            wraplength=500, justify='left')
    description.pack(fill='x', pady=(5, 0))
    text.bind('<Configure>', lambda event: description.configure(wraplength=max(1, event.width)), add='+')
    text.bind('<Configure>', lambda event: title_label.configure(wraplength=max(1, event.width)), add='+')
    return frame


def card(parent, padding=16):
    return ttk.Frame(parent, style='Surface.TFrame', padding=padding)


class ScrollableBody(ttk.Frame):
    """Scoped scrolling: another tab or window never steals wheel events."""
    def __init__(self, parent):
        super().__init__(parent)
        self.pack(fill='both', expand=True, padx=20, pady=(0, 16))
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        bar.pack(side='right', fill='y')
        self.canvas.configure(yscrollcommand=bar.set)
        self.frame = ttk.Frame(self.canvas)
        self._item = self.canvas.create_window(0, 0, anchor='nw', window=self.frame)
        self._closed = False
        self._owner = self.winfo_toplevel()
        self._bindings = []
        self.canvas.bind('<Configure>', self._resize)
        self.frame.bind('<Configure>', self._region)
        for sequence, callback in (('<MouseWheel>', self._wheel), ('<Button-4>', self._wheel),
                                   ('<Button-5>', self._wheel), ('<FocusIn>', self._reveal)):
            self._bindings.append((sequence, self._owner.bind(sequence, callback, add='+')))
        self.bind('<Destroy>', self._destroyed, add='+')

    def _contains(self, widget):
        while widget is not None:
            if widget is self.frame:
                return True
            widget = getattr(widget, 'master', None)
        return False

    def _resize(self, event):
        if not self._closed:
            self.canvas.itemconfigure(self._item, width=max(1, event.width))

    def _region(self, _event=None):
        if not self._closed:
            self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _wheel(self, event):
        if self._closed or not self._contains(event.widget) or not self.winfo_viewable():
            return
        units = -1 if getattr(event, 'num', None) == 4 else (1 if getattr(event, 'num', None) == 5 else -int(event.delta / 120))
        if units:
            self.canvas.yview_scroll(units * 3, 'units')
        return 'break'

    def _reveal(self, event):
        if self._closed or not self._contains(event.widget) or not self.winfo_viewable():
            return
        y = event.widget.winfo_rooty() - self.frame.winfo_rooty()
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        height = event.widget.winfo_height()
        if y < top or y + height > bottom:
            target = max(0, y - 8) if y < top else max(0, y + height + 8 - self.canvas.winfo_height())
            self.canvas.yview_moveto(target / max(1, self.frame.winfo_height()))

    def _destroyed(self, event):
        if event.widget is self:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        for sequence, binding in self._bindings:
            try:
                self._owner.unbind(sequence, binding)
            except tk.TclError:
                pass
        self._bindings.clear()
        self._owner = None
