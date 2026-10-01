"""Gallery selection gestures, kept separate from native file drag-and-drop."""
import tkinter as tk
import sys
from ui import ACCENT, FIELD, MUTED
from selection_overlay import SelectionOverlay


def _pointer_snapshot():
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes
    try:
        user = ctypes.windll.user32
        user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user.GetCursorPos.restype = wintypes.BOOL
        position = wintypes.POINT()
        if not user.GetCursorPos(ctypes.byref(position)):
            return None
        button = 2 if user.GetSystemMetrics(23) else 1  # SM_SWAPBUTTON
        return position.x, position.y, bool(user.GetAsyncKeyState(button) & 0x8000)
    except OSError:
        return None


class GallerySelection:
    def setup_selection(self):
        self.anchor_key = None
        self.drag_start = None
        self.marquee = None
        self._marquee_timer = None
        self._selection_bindings = []
        self._selection_event_tag = f'VideoCatchSelection_{id(self)}'
        self._selection_tag_bindings = []
        self._selection_overlay = SelectionOverlay(self.canvas, ACCENT) if sys.platform == 'win32' else None
        self._selection_overlay_error_reported = False
        self._selection_edges = [] if self._selection_overlay else [tk.Frame(self.canvas, bg=ACCENT) for _ in range(4)]
        for widget in (self.canvas, self.grid):
            widget.bind('<ButtonPress-1>', self.begin_marquee)
        # Widget/class handlers can return "break" before the toplevel sees a
        # motion or release (notably the name Checkbutton). An active marquee
        # owns these events even when the pointer crosses a card's children.
        for event, handler in (('<B1-Motion>', self._selection_tag_motion),
                               ('<ButtonRelease-1>', self._selection_tag_release),
                               ('<Motion>', self._selection_tag_recover)):
            token = self.window.bind_class(self._selection_event_tag, event, handler)
            self._selection_tag_bindings.append((event, token))
        self.bind_selection_widget(self.canvas)
        for event, handler in (('<B1-Motion>', self.update_marquee),
                               ('<ButtonRelease-1>', self.selection_pointer_release),
                               ('<Escape>', self.escape_selection),
                               ('<Motion>', self.recover_pointer_release),
                               ('<FocusOut>', self.selection_focus_out),
                               ('<Unmap>', self.selection_unmapped)):
            self._selection_bindings.append((event, self.window.bind(event, handler, add='+')))
        self.canvas.bind('<Control-a>', lambda _: self._select_all_key())
        self.canvas.bind('<Delete>', lambda _: self.delete_selected())

    def bind_selection_widget(self, widget):
        tag = getattr(self, '_selection_event_tag', None)
        if tag is not None and tag not in widget.bindtags():
            widget.bindtags((tag, *widget.bindtags()))
        for child in widget.winfo_children():
            self.bind_selection_widget(child)

    def _selection_tag_motion(self, event):
        if self.marquee is not None:
            return self.update_marquee(event)

    def _selection_tag_release(self, event):
        if self.marquee is not None:
            return self.selection_pointer_release(event)

    def _selection_tag_recover(self, event):
        if self.marquee is not None:
            return self.recover_pointer_release(event)

    def recover_pointer_release(self, event):
        if getattr(self, 'marquee', None) and self.marquee.get('physical'):
            snapshot = _pointer_snapshot()
            if snapshot is not None:
                if not snapshot[2]:
                    self.marquee['pointer'] = snapshot[:2]
                    self._paint_marquee()
                    self.cancel_gesture()
                return
        if not event.state & 0x100:
            self.cancel_gesture()

    def _native_drag_active(self):
        return bool(getattr(getattr(self, 'app', None), 'native_drag_active', False))

    def _cancel_native_drag(self):
        cancel = getattr(getattr(self, 'app', None), 'cancel_native_drag', None)
        if self._native_drag_active() and callable(cancel):
            cancel()

    def cancel_gesture(self, cancel_marquee=False):
        self.drag_start = None
        return self.end_marquee(cancel=cancel_marquee)

    def selection_pointer_release(self, event):
        # Cover bindings run first and own click-to-collapse. This handler only
        # finishes capture, so a completed native drag preserves the group.
        active = getattr(self, 'marquee', None) is not None
        try:
            if active:
                self.end_marquee(event)
        finally:
            self.cancel_gesture()
        return 'break' if active else None

    def selection_focus_out(self, event):
        if self.window.focus_displayof() is None:
            self.cancel_gesture()

    def selection_unmapped(self, event):
        if event.widget in (self.window, self.canvas, self.frame):
            self._cancel_native_drag()
            self.cancel_gesture()

    def _select_all_key(self):
        if self._native_drag_active():
            return 'break'
        self.select_page()
        return 'break'

    def selection_changed(self):
        count = len(self.selected_keys())
        if hasattr(self, 'selection_caption'):
            self.selection_caption.set(f'已选 {count} 项' if count else '尚未选择素材')
            for button in self.batch_buttons:
                button.configure(state='normal' if count and not self.acting and not self._native_drag_active() else 'disabled')

    def clear_selection(self):
        self.cancel_gesture()
        if self._native_drag_active():
            return 'break'
        for variable in self.selection.values():
            if variable.get():
                variable.set(False)
        self.anchor_key = None
        self.selection_changed()

    def select_card(self, key, state=0, preserve_group=False):
        self.cancel_gesture(cancel_marquee=True)
        if self._native_drag_active():
            return 'break'
        self.canvas.focus_set()
        keys = list(self.cards)
        if state & 1 and self.anchor_key in keys:
            a, b = sorted((keys.index(self.anchor_key), keys.index(key)))
            hits = set(keys[a:b + 1])
            if state & 4:
                hits.update(self.selected_keys())
            for candidate, variable in self.selection.items():
                variable.set(candidate in hits)
        elif state & 4:
            self.selection[key].set(not self.selection[key].get())
            self.anchor_key = key
        elif not (preserve_group and self.selection[key].get()):
            for candidate, variable in self.selection.items():
                variable.set(candidate == key)
            self.anchor_key = key
        self.selection_changed()
        return 'break'

    def toggle_card(self, key, event):
        return self.select_card(key, event.state if event.state & 1 else event.state | 4)

    def release_cover(self, event):
        start = getattr(self, 'drag_start', None)
        self.cancel_gesture()
        if start and not self._native_drag_active() and not event.state & 5 and start[2] in self.cards:
            self.select_card(start[2])

    def _point(self, x_root, y_root):
        return (self.canvas.canvasx(x_root - self.canvas.winfo_rootx()),
                self.canvas.canvasy(y_root - self.canvas.winfo_rooty()))

    def begin_card_selection(self, event, key):
        return self.begin_marquee(event, key=key)

    def begin_marquee(self, event, key=None):
        self.cancel_gesture(cancel_marquee=True)
        if self.closed or self._native_drag_active() or self.canvas.grab_current() is not None:
            return
        self.canvas.focus_set()
        original = set(self.selected_keys())
        add = bool(event.state & 5)
        click_selection = None
        if key is not None:
            # The card body acts as an ordinary click until the drag threshold
            # is crossed. Covers and action buttons keep their own gestures.
            self.select_card(key, event.state)
            click_selection = set(self.selected_keys())
        elif not add:
            # clear_selection also cancels gestures: clear before assigning the
            # new marquee, while retaining the pre-click selection for Escape.
            self.clear_selection()
        snapshot = _pointer_snapshot()
        # A queued press can arrive after the pointer has already moved.
        physical = bool(snapshot and snapshot[2])
        self.marquee = {'start': self._point(event.x_root, event.y_root),
                        'pointer': (event.x_root, event.y_root),
                        'origin': (event.x_root, event.y_root), 'dragging': False,
                        'original': original, 'add': add, 'physical': physical}
        if click_selection is not None:
            self.marquee['click_selection'] = click_selection
        self.canvas.grab_set()
        self._marquee_tick()
        return 'break'

    def update_marquee(self, event):
        if self._native_drag_active():
            self.cancel_gesture(cancel_marquee=True)
            return 'break'
        if self.marquee is not None:
            snapshot = _pointer_snapshot() if self.marquee.get('physical') else None
            pointer = snapshot[:2] if snapshot is not None else (event.x_root, event.y_root)
            self.marquee['pointer'] = pointer
            origin = self.marquee['origin']
            self.marquee['dragging'] = abs(pointer[0]-origin[0]) + abs(pointer[1]-origin[1]) > 5
            self._paint_marquee()
            return 'break'

    def _paint_marquee(self):
        if self._native_drag_active():
            self.cancel_gesture(cancel_marquee=True)
            return
        gesture = getattr(self, 'marquee', None)
        if not gesture or self.closed:
            return
        x, y = self._point(*gesture['pointer'])
        sx, sy = gesture['start']
        left, right = sorted((sx, x))
        top, bottom = sorted((sy, y))
        hits = set(gesture['original']) if gesture['add'] else set()
        if 'click_selection' in gesture and not gesture['dragging']:
            hits = set(gesture['click_selection'])
        elif right - left > 3 or bottom - top > 3:
            for key, card in self.cards.items():
                cx, cy = self._point(card.winfo_rootx(), card.winfo_rooty())
                if cx < right and cx + card.winfo_width() > left and cy < bottom and cy + card.winfo_height() > top:
                    hits.add(key)
        for key, variable in self.selection.items():
            value = key in hits
            if variable.get() != value:
                variable.set(value)
        # A fixed Windows drawing surface avoids moving child HWNDs across the
        # embedded card grid. Only its single rectangle changes during motion.
        ox, oy = self.canvas.canvasx(0), self.canvas.canvasy(0)
        l, r = max(0, left-ox), min(self.canvas.winfo_width()-2, right-ox)
        t, b = max(0, top-oy), min(self.canvas.winfo_height()-2, bottom-oy)
        overlay = getattr(self, '_selection_overlay', None)
        if overlay is not None:
            overlay.show((l, t, r, b))
            if self.marquee is not gesture or self.closed:
                overlay.hide()
                return
            if overlay.last_error and not self._selection_overlay_error_reported:
                self._selection_overlay_error_reported = True
                status = getattr(self, 'status', None)
                if status is not None:
                    status.set('框选边框显示失败；仍可选择素材，请重启拾影恢复显示。')
        elif r-l > 3 and b-t > 3:
            for edge, rect in zip(self._selection_edges, ((l,t,r-l,2), (l,b,r-l,2), (l,t,2,b-t), (r,t,2,b-t))):
                edge.place(x=round(rect[0]), y=round(rect[1]), width=max(2,round(rect[2])), height=max(2,round(rect[3])))
                edge.lift()
        else:
            for edge in self._selection_edges:
                edge.place_forget()

    def _marquee_tick(self):
        self._marquee_timer = None
        if self._native_drag_active():
            self.cancel_gesture(cancel_marquee=True)
            return
        if self.marquee is None or self.closed:
            return
        gesture = self.marquee
        if self.marquee.get('physical'):
            # Transparent owned windows and card bindings can suppress Tk
            # motion delivery across widgets. Sample on the UI thread during
            # a real held-button gesture; synthetic events retain their data.
            snapshot = _pointer_snapshot()
            if snapshot is not None:
                pointer = snapshot[:2]
                self.marquee['pointer'] = pointer
                origin = self.marquee['origin']
                self.marquee['dragging'] = abs(pointer[0]-origin[0]) + abs(pointer[1]-origin[1]) > 5
                if not snapshot[2]:
                    self._paint_marquee()
                    self.end_marquee()
                    return
        y = self.marquee['pointer'][1] - self.canvas.winfo_rooty()
        direction = -1 if y < 22 else 1 if y > self.canvas.winfo_height()-22 else 0
        if direction and self.marquee['dragging']:
            self.canvas.yview_scroll(direction, 'units')
        self._paint_marquee()
        if self.marquee is gesture and not self.closed:
            self._marquee_timer = self.window.after(40, self._marquee_tick)

    def end_marquee(self, event=None, cancel=False):
        gesture = getattr(self, 'marquee', None)
        canvas = getattr(self, 'canvas', None)
        window = getattr(self, 'window', None)
        try:
            if gesture is not None and event is not None and canvas is not None and not self._native_drag_active():
                self.update_marquee(event)
        finally:
            self.marquee = None
            timer = getattr(self, '_marquee_timer', None)
            self._marquee_timer = None
            if timer is not None and window is not None:
                try:
                    window.after_cancel(timer)
                except tk.TclError:
                    pass
            try:
                if canvas is not None and canvas.grab_current() is canvas:
                    canvas.grab_release()
            except tk.TclError:
                pass
            overlay = getattr(self, '_selection_overlay', None)
            if overlay is not None:
                overlay.hide()
            for edge in getattr(self, '_selection_edges', ()):
                try:
                    edge.place_forget()
                except tk.TclError:
                    pass  # Still clear other surviving edges during teardown.
            selection = getattr(self, 'selection', None)
            if cancel and gesture is not None and selection is not None:
                for key, variable in selection.items():
                    if variable.get() != (key in gesture['original']):
                        variable.set(key in gesture['original'])
                self.selection_changed()
        return 'break' if gesture is not None else None

    def escape_selection(self, event):
        if self._native_drag_active():
            self._cancel_native_drag()
            self.cancel_gesture(cancel_marquee=True)
            return 'break'
        if self.marquee is not None:
            return self.cancel_gesture(cancel_marquee=True)
        self.cancel_gesture()
        if event.widget is self.canvas:
            self.clear_selection()
            return 'break'

    def hover_card(self, key, active):
        card = self.cards.get(key)
        if card and not self.selection[key].get():
            card.configure(highlightbackground='#8D759F' if active else FIELD)

    def teardown_selection(self):
        self.cancel_gesture()
        overlay = getattr(self, '_selection_overlay', None)
        if overlay is not None:
            overlay.close()
        for event, token in getattr(self, '_selection_bindings', ()):
            self.window.unbind(event, token)
        self._selection_bindings = []
        tag = getattr(self, '_selection_event_tag', None)
        for event, token in getattr(self, '_selection_tag_bindings', ()):
            self.window.unbind_class(tag, event)
            self.window.deletecommand(token)
        self._selection_tag_bindings = []
