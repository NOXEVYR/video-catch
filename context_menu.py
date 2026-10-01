"""Compact, themed context menus scoped to an owning Tk window.

Keep one ContextMenu(owner), then call show(x_root, y_root, title, items).
Items are dictionaries with label, command, enabled, shortcut, danger and icon;
{'separator': True} adds a rule. show returns the popup; close is idempotent.
There is no nested event loop, scheduled callback, native menu or global grab.
"""
import tkinter as tk
from tkinter import font as tkfont, ttk
import weakref

from surface_ui import owner_work_area
from ui import PANEL, FIELD, FG, MUTED, ACCENT, LINE


def _alive(widget):
    try:
        return widget is not None and bool(widget.winfo_exists())
    except tk.TclError:
        return False


class ContextMenu:
    def __init__(self, owner):
        self._owner_ref = weakref.ref(owner)
        self.window = None
        self.buttons = []
        self._rows = []
        self._commands = []
        self._bindings = []
        self._previous_focus = self._previous_grab = None
        self._active = None
        self._canvas = self._content = None

    def show(self, x, y, title, items):
        """Show at absolute desktop coordinates, constrained to owner's monitor."""
        self.close()
        owner = self._owner_ref()
        if not _alive(owner):
            return None
        top = owner.winfo_toplevel()
        try:
            self._previous_focus = top.focus_get()
            self._previous_grab = top.grab_current()
            window = self.window = tk.Toplevel(top)
            window.withdraw()
            window.overrideredirect(True)
            if top.winfo_viewable():
                window.transient(top)
            window.configure(bg=LINE, borderwidth=0)
            scale = max(1.0, window.winfo_fpixels('1i') / 96)
            pad = max(6, round(9 * scale))
            bounds = owner_work_area(top)
            available_w = max(1, (bounds[2] - bounds[0] - 8) if bounds else
                              window.winfo_screenwidth() - 8)
            available_h = max(1, (bounds[3] - bounds[1] - 8) if bounds else
                              window.winfo_screenheight() - 48)
            font = tkfont.Font(window, family='Microsoft YaHei UI', size=9)
            items = [dict(item) for item in items]
            desired_w = max([round(280 * scale)] + [
                font.measure(str(item.get('label', ''))) +
                font.measure(str(item.get('shortcut', ''))) + round(76 * scale)
                for item in items if not item.get('separator')])
            width = min(desired_w, round(420 * scale), available_w)
            panel = tk.Frame(window, bg=PANEL)
            panel.pack(fill='both', expand=True, padx=1, pady=1)
            if title:
                tk.Label(panel, text=str(title), bg=PANEL, fg=MUTED,
                         font=('Microsoft YaHei UI', 9, 'bold'), anchor='w',
                         justify='left', wraplength=max(1, width - 2 * pad - 2)
                         ).pack(fill='x', padx=pad, pady=(pad, max(4, pad // 2)))
                tk.Frame(panel, bg=LINE, height=1).pack(fill='x', padx=pad)
            body = tk.Frame(panel, bg=PANEL)
            body.pack(fill='both', expand=True, padx=pad // 2, pady=pad // 2)
            canvas = self._canvas = tk.Canvas(body, bg=PANEL, borderwidth=0,
                                             highlightthickness=0, width=width)
            canvas.pack(side='left', fill='both', expand=True)
            bar = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
            canvas.configure(yscrollcommand=bar.set)
            content = self._content = tk.Frame(canvas, bg=PANEL)
            content.columnconfigure(0, weight=1)
            content_id = canvas.create_window(0, 0, anchor='nw', window=content)
            canvas.bind('<Configure>', lambda event: canvas.itemconfigure(
                content_id, width=max(1, event.width)))
            content.bind('<Configure>', lambda _event: canvas.configure(
                scrollregion=canvas.bbox('all')))
            row_index = 0
            for item in items:
                if item.get('separator'):
                    tk.Frame(content, bg=LINE, height=1).grid(
                        row=row_index, column=0, sticky='ew', padx=pad // 2, pady=pad // 2)
                else:
                    self._add_row(content, row_index, item, font, width, pad, scale)
                row_index += 1
            window.update_idletasks()
            header_h = panel.winfo_reqheight() - body.winfo_reqheight()
            viewport_h = min(content.winfo_reqheight(), max(1, available_h - header_h - pad - 2))
            if viewport_h < content.winfo_reqheight():
                bar.pack(side='right', fill='y')
            canvas.configure(height=viewport_h)
            # Measure again after wrapping and reserving the scrollbar.
            window.update_idletasks()
            height = min(available_h, panel.winfo_reqheight() + 2)
            if bounds is not None:
                left, top_y, right, bottom = bounds
                x = max(left + 4, min(int(x), right - width - 4))
                y = max(top_y + 4, min(int(y), bottom - height - 4))
            # '+-N' means an absolute negative monitor coordinate in Tk.
            window.geometry(f'{width}x{height}+{int(x)}+{int(y)}')
            for target in (owner,) if owner is top else (owner, top):
                for sequence, callback in (('<Destroy>', self._owner_destroyed),
                                           ('<Unmap>', self._owner_hidden)):
                    self._bindings.append((weakref.ref(target), sequence,
                                           target.bind(sequence, callback, add='+')))
            window.bind('<Destroy>', self._destroyed, add='+')
            window.bind('<FocusOut>', self._focus_out, add='+')
            window.bind('<Escape>', self._escape)
            window.bind('<Down>', lambda _event: self._move(1))
            window.bind('<Up>', lambda _event: self._move(-1))
            window.bind('<Return>', self._enter)
            window.bind('<KP_Enter>', self._enter)
            for sequence in ('<ButtonPress-1>', '<ButtonPress-2>', '<ButtonPress-3>'):
                window.bind(sequence, self._outside_click, add='+')
            for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                window.bind(sequence, self._wheel, add='+')
            window.deiconify()
            window.update_idletasks()
            window.grab_set()
            window.focus_set()
            self._move(1)
            return window
        except Exception:
            self.close()
            raise

    def _add_row(self, content, row_index, item, font, width, pad, scale):
        index = len(self.buttons)
        enabled = bool(item.get('enabled', True))
        color = ('#eda6b1' if item.get('danger') else FG) if enabled else MUTED
        row = tk.Frame(content, bg=PANEL)
        row.grid(row=row_index, column=0, sticky='ew')
        row.columnconfigure(1, weight=1)
        icon = tk.Label(row, text=str(item.get('icon') or '·'), width=2,
                        bg=PANEL, fg=color, font=font)
        icon.grid(row=0, column=0, padx=(pad // 2, 2))
        shortcut = str(item.get('shortcut') or '')
        # Keep shortcuts subordinate and bounded even for caller-supplied text.
        limit = max(1, min(round(100 * scale), width // 3))
        while len(shortcut) > 1 and font.measure(shortcut) > limit:
            shortcut = shortcut[:-2] + '…'
        hint = tk.Label(row, text=shortcut, bg=PANEL, fg=MUTED, font=font, anchor='e')
        hint.grid(row=0, column=2, padx=(pad, pad // 2))
        button = tk.Button(row, text=str(item.get('label') or ''), font=font,
                           anchor='w', justify='left', bg=PANEL, fg=color,
                           activebackground=FIELD, activeforeground=color,
                           disabledforeground=MUTED, relief='flat', borderwidth=0,
                           highlightthickness=0, padx=2, pady=max(5, round(7 * scale)),
                           takefocus=False, state='normal' if enabled else 'disabled',
                           command=lambda: self._activate(index),
                           wraplength=max(1, width - round(84 * scale) - font.measure(shortcut)))
        button.grid(row=0, column=1, sticky='ew')
        self.buttons.append(button)
        self._rows.append((row, icon, button, hint))
        self._commands.append(item.get('command'))
        for widget in (row, icon, button, hint):
            widget.bind('<Enter>', lambda _event, value=index: self._select(value))
        for widget in (row, icon, hint):
            widget.bind('<ButtonRelease-1>', lambda _event, value=index: self._activate(value))

    def _select(self, index):
        if index >= len(self.buttons) or str(self.buttons[index]['state']) == 'disabled':
            return
        self._active = index
        for value, widgets in enumerate(self._rows):
            for widget in widgets:
                widget.configure(bg=FIELD if value == index else PANEL)

    def _move(self, step):
        eligible = [index for index, button in enumerate(self.buttons)
                    if str(button['state']) != 'disabled']
        if eligible:
            position = eligible.index(self._active) if self._active in eligible else (-1 if step > 0 else 0)
            self._select(eligible[(position + step) % len(eligible)])
            row = self._rows[self._active][0]
            y, height = row.winfo_y(), row.winfo_height()
            top = self._canvas.canvasy(0)
            bottom = top + self._canvas.winfo_height()
            if y < top or y + height > bottom:
                target = y if y < top else y + height - self._canvas.winfo_height()
                self._canvas.yview_moveto(max(0, target) / max(1, self._content.winfo_height()))
        return 'break'

    def _enter(self, _event=None):
        if self._active is not None:
            self._activate(self._active)
        return 'break'

    def _activate(self, index):
        if index >= len(self.buttons) or str(self.buttons[index]['state']) == 'disabled':
            return 'break'
        command = self._commands[index]
        self.close()
        if callable(command):
            command()
        return 'break'

    def _outside_click(self, event):
        window = self.window
        if window is not None and not (
                window.winfo_rootx() <= event.x_root < window.winfo_rootx() + window.winfo_width() and
                window.winfo_rooty() <= event.y_root < window.winfo_rooty() + window.winfo_height()):
            self.close()
            return 'break'

    def _wheel(self, event):
        if self._canvas is not None:
            units = -1 if getattr(event, 'num', None) == 4 else (
                1 if getattr(event, 'num', None) == 5 else -int(event.delta / 120))
            self._canvas.yview_scroll(units * 2, 'units')
        return 'break'

    def _escape(self, _event=None):
        self.close()
        return 'break'

    def _focus_out(self, _event=None):
        window = self.window
        if window is not None:
            focus = window.focus_get()
            if focus is None or focus.winfo_toplevel() is not window:
                self.close(restore_focus=False)

    def _owner_hidden(self, event):
        if any(event.widget is reference() for reference, _, _ in self._bindings):
            self.close(restore_focus=False)

    def _owner_destroyed(self, event):
        if any(event.widget is reference() for reference, _, _ in self._bindings):
            self.close(restore_focus=False)

    def _destroyed(self, event):
        if event.widget is self.window:
            self.close(restore_focus=False)

    def close(self, *, restore_focus=True):
        """Release only this menu's grab, clean callbacks, then restore local state."""
        window, self.window = self.window, None
        previous_focus, self._previous_focus = self._previous_focus, None
        previous_grab, self._previous_grab = self._previous_grab, None
        owned_focus = False
        owner = self._owner_ref()
        if _alive(window):
            try:
                focus = window.focus_get()
                owned_focus = focus is not None and focus.winfo_toplevel() is window
                if window.grab_current() is window:
                    window.grab_release()
            except tk.TclError:
                pass
        # Destroy events arrive after the popup ceases to exist. Query its
        # owner instead so an earlier locally modal window also recovers then.
        if _alive(owner) and _alive(previous_grab):
            try:
                current = owner.grab_current()
                if previous_grab.winfo_viewable() and current in (None, window):
                    previous_grab.grab_set()
            except tk.TclError:
                pass
        for reference, sequence, binding in self._bindings:
            target = reference()
            if _alive(target):
                target.unbind(sequence, binding)
        self._bindings.clear()
        self.buttons.clear()
        self._rows.clear()
        self._commands.clear()
        self._active = None
        self._canvas = self._content = None
        if _alive(window):
            window.destroy()
        if restore_focus and owned_focus and _alive(previous_focus):
            try:
                if previous_focus.winfo_viewable():
                    previous_focus.focus_set()
            except tk.TclError:
                pass
