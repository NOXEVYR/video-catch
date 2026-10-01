"""Offscreen Tk gesture routing; no real pointer, keys, OLE or private media."""
import gc
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from library_ui import LibraryWindow
from context_menu import ContextMenu


class LibraryDirectionalTkTests(unittest.TestCase):
    def setUp(self):
        self.root = self.gallery = None
        self.temporary = tempfile.TemporaryDirectory(prefix='videocatch-selection-fixture-')
        self.addCleanup(self.cleanup)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk unavailable: {error}')
        self.root.geometry('800x700+12000+12000')
        self.errors = []
        self.event_time = 1000
        self.root.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        g = self.gallery = LibraryWindow.__new__(LibraryWindow)
        g.window = g.frame = self.root
        g.context_menu = ContextMenu(self.root)
        g.canvas = tk.Canvas(self.root, borderwidth=0, highlightthickness=0)
        g.canvas.pack(fill='both', expand=True)
        g.grid = ttk.Frame(g.canvas)
        g.content = g.canvas.create_window(0, 0, window=g.grid, anchor='nw', width=800)
        g.app = SimpleNamespace(native_drag_active=False, start_native_drag=Mock(),
                                cancel_native_drag=Mock(), media_path_busy=lambda path: False)
        g.cards, g.selection, g.photos, g.labels = {}, {}, {}, {}
        g.closed = g.acting = False
        g.columns, g.page, g.generation = 2, 0, 0
        g._signature, g._selection_traces = None, []
        g.requests = queue.Queue()
        g.status, g.query = tk.StringVar(self.root), tk.StringVar(self.root)
        g.media_type = tk.StringVar(self.root, value='全部类型')
        g.source = tk.StringVar(self.root, value='全部来源')
        g.sort = tk.StringVar(self.root, value='最近保存')
        g.model = SimpleNamespace(error='', unconfirmed_paths=set(), entries={
            key: {'name': key, 'path': str(Path(self.temporary.name) / f'{key}.png'),
                  'source': 'import', 'media_type': 'image', 'added': 0, 'hidden': False}
            for key in 'abcd'})
        g.rows = lambda: list(g.model.entries.items())
        self.pointer = patch('library_selection._pointer_snapshot', return_value=None)
        self.snapshot = self.pointer.start()
        self.addCleanup(self.pointer.stop)
        g.setup_selection()
        g.render()
        self.root.update()

    def cleanup(self):
        try:
            if self.gallery is not None:
                self.gallery.context_menu.close()
                self.gallery.teardown_selection()
                self.gallery._clear_selection_traces()
            if self.root is not None:
                try:
                    self.root.destroy()
                except tk.TclError:
                    pass
        finally:
            # Destroying a window alone leaves Tcl references in Python widget
            # cycles. Collect them on this fixture's UI thread, before another
            # test's HTTP worker could collect them on the wrong thread.
            self.gallery = self.root = None
            gc.collect()
            self.temporary.cleanup()

    def event(self, widget, kind, point, state=0):
        x, y = point
        # Separate gestures in synthetic time; otherwise Tk interprets rapid
        # test presses at the same cover as its legitimate Double-1 playback.
        self.event_time += 1000
        widget.event_generate(kind, x=x-widget.winfo_rootx(), y=y-widget.winfo_rooty(),
                              rootx=x, rooty=y, state=state, time=self.event_time)
        self.root.update_idletasks()
        self.assertEqual(self.errors, [])

    def point(self, x, y):
        return self.gallery.canvas.winfo_rootx()+x, self.gallery.canvas.winfo_rooty()+y

    def center(self, widget):
        return widget.winfo_rootx()+widget.winfo_width()//2, widget.winfo_rooty()+widget.winfo_height()//2

    def assert_finished(self):
        g = self.gallery
        self.assertIsNone(g.marquee)
        self.assertIsNone(g.drag_start)
        self.assertIsNone(g._marquee_timer)
        self.assertIsNone(g.canvas.grab_current())
        if g._selection_overlay is not None:
            self.assertFalse(g._selection_overlay.visible)
        self.assertTrue(all(not edge.winfo_ismapped() for edge in g._selection_edges))

    def outline(self):
        g = self.gallery
        if g._selection_overlay is not None:
            self.assertTrue(g._selection_overlay.visible, g._selection_overlay.last_error)
            return g._selection_overlay._canvas.coords(g._selection_overlay._rectangle)
        self.assertTrue(all(edge.winfo_ismapped() for edge in g._selection_edges))
        return None

    def tick(self):
        g = self.gallery
        if g._marquee_timer is not None:
            g.window.after_cancel(g._marquee_timer)
        g._marquee_tick()
        self.root.update_idletasks()
        self.assertEqual(self.errors, [])

    def test_all_four_directions_from_canvas_and_grid_blank(self):
        g = self.gallery
        for widget in (g.canvas, g.grid):
            bottom = g.grid.winfo_height()-2 if widget is g.grid else g.canvas.winfo_height()-30
            corners = ((2, 2), (798, 2), (2, bottom), (798, bottom))
            for index, start in enumerate(corners):
                with self.subTest(widget=widget.winfo_class(), direction=index):
                    end = corners[3-index]
                    self.event(widget, '<ButtonPress-1>', self.point(*start))
                    self.assertIs(g.canvas.grab_current(), g.canvas)
                    self.event(g.canvas, '<B1-Motion>', self.point(*end), 0x100)
                    self.assertEqual(g.selected_keys(), list('abcd'))
                    coords = self.outline()
                    if coords is not None:
                        self.assertEqual(coords, [2., 2., 798., float(bottom)])
                    self.event(g.canvas, '<ButtonRelease-1>', self.point(*end), 0x100)
                    self.assert_finished()
        g.app.start_native_drag.assert_not_called()

    def test_cross_child_motion_and_release_before_widget_break_handlers(self):
        g = self.gallery
        card = g.cards['a']
        cover, name, metadata, actions = card.winfo_children()[:4]
        children = (card, cover, name, metadata, actions, *actions.winfo_children())
        for child in children:
            with self.subTest(child=child.winfo_class()):
                self.event(g.grid, '<ButtonPress-1>', self.point(2, 2))
                end = self.center(child)
                self.event(child, '<B1-Motion>', end, 0x100)
                self.assertIsNotNone(g.marquee)
                self.assertEqual(g.selected_keys(), ['a'])
                self.outline()
                self.event(child, '<ButtonRelease-1>', end, 0x100)
                self.assert_finished()
        g.app.start_native_drag.assert_not_called()

    def test_child_release_applies_final_position_without_last_motion(self):
        g = self.gallery
        end_widget = g.cards['d'].winfo_children()[1]  # Name has its own break.
        self.event(g.grid, '<ButtonPress-1>', self.point(2, 2))
        self.event(g.canvas, '<B1-Motion>', self.center(g.cards['a']), 0x100)
        self.assertEqual(g.selected_keys(), ['a'])
        self.event(end_widget, '<ButtonRelease-1>', self.center(end_widget), 0x100)
        self.assertEqual(g.selected_keys(), list('abcd'))
        self.assert_finished()

    def test_card_body_metadata_and_action_blank_click_then_drag(self):
        g = self.gallery
        for key in 'abcd':
            card = g.cards[key]
            metadata, actions = card.winfo_children()[2:4]
            for widget in (card, metadata, actions):
                with self.subTest(key=key, widget=widget.winfo_class()):
                    start = ((card.winfo_rootx()+1, card.winfo_rooty()+1)
                             if widget is card else self.center(widget))
                    self.event(widget, '<ButtonPress-1>', start)
                    self.assertEqual(g.selected_keys(), [key])
                    self.assertIsNotNone(g.marquee)
                    self.event(widget, '<ButtonRelease-1>', start, 0x100)
                    self.assertEqual(g.selected_keys(), [key])
                    self.assert_finished()
                    self.event(widget, '<ButtonPress-1>', start)
                    end = self.point(798 if key in 'ac' else 2, 650 if key in 'ab' else 2)
                    self.event(g.canvas, '<B1-Motion>', end, 0x100)
                    self.assertEqual(g.selected_keys(), list('abcd'))
                    self.outline()
                    self.event(g.canvas, '<ButtonRelease-1>', end, 0x100)
                    self.assert_finished()
        g.app.start_native_drag.assert_not_called()

    def test_card_modified_click_and_escape_preserve_selection_rules(self):
        g = self.gallery
        card = g.cards['b']
        start = (card.winfo_rootx()+1, card.winfo_rooty()+1)
        g.select_card('a')
        self.event(card, '<ButtonPress-1>', start, 4)
        self.event(card, '<ButtonRelease-1>', start, 0x104)
        self.assertEqual(g.selected_keys(), ['a', 'b'])
        self.assert_finished()
        self.event(card, '<ButtonPress-1>', start, 4)
        self.event(card, '<ButtonRelease-1>', start, 0x104)
        self.assertEqual(g.selected_keys(), ['a'])
        self.event(card, '<ButtonPress-1>', start, 0)
        self.assertEqual(g.selected_keys(), ['b'])
        g.escape_selection(SimpleNamespace(widget=g.canvas))
        self.assertEqual(g.selected_keys(), ['a'])
        self.assert_finished()

    def test_physical_sampling_four_directions_ignores_stale_child_motion(self):
        g = self.gallery
        corners = ((2, 2), (798, 2), (2, 650), (798, 650))
        child = g.cards['a'].winfo_children()[1]
        for index, start in enumerate(corners):
            with self.subTest(direction=index):
                end = corners[3-index]
                self.snapshot.return_value = (*self.point(*start), True)
                self.event(g.grid if start[1] == 2 else g.canvas,
                           '<ButtonPress-1>', self.point(*start))
                self.assertTrue(g.marquee['physical'])
                self.snapshot.return_value = (*self.point(*end), True)
                self.event(child, '<B1-Motion>', self.point(*start), 0x100)
                self.assertEqual(g.marquee['pointer'], self.point(*end))
                self.assertEqual(g.selected_keys(), list('abcd'))
                self.outline()
                self.snapshot.return_value = (*self.point(*end), False)
                self.tick()  # Missed release is recovered without any OS input.
                self.assert_finished()

    def test_reverse_shrink_cancel_rebuild_hide_and_focus_loss(self):
        g = self.gallery
        for lifecycle in ('escape', 'rebuild', 'hide', 'focus'):
            with self.subTest(lifecycle=lifecycle):
                g.select_card('d')
                self.event(g.grid, '<ButtonPress-1>', self.point(2, 2))
                self.event(g.canvas, '<B1-Motion>', self.point(798, 650), 0x100)
                self.assertEqual(g.selected_keys(), list('abcd'))
                self.event(g.canvas, '<B1-Motion>', self.point(100, 100), 0x100)
                self.assertEqual(g.selected_keys(), ['a'])
                coords = self.outline()
                if coords is not None:
                    self.assertEqual(coords, [2., 2., 100., 100.])
                if lifecycle == 'escape':
                    g.escape_selection(SimpleNamespace(widget=g.canvas))
                    self.assertEqual(g.selected_keys(), ['d'])
                elif lifecycle == 'rebuild':
                    g.model.entries['a']['name'] += ' changed'
                    g.render()
                    self.assertEqual(g.selected_keys(), ['d'])
                elif lifecycle == 'hide':
                    self.root.withdraw()
                    self.root.update()
                    self.root.deiconify()
                    self.root.update()
                else:
                    with patch.object(g.window, 'focus_displayof', return_value=None):
                        g.selection_focus_out(SimpleNamespace(widget=g.canvas))
                self.assert_finished()

    def test_cover_repeated_drag_keeps_group_and_never_starts_marquee(self):
        g = self.gallery
        cover = g.cards['a'].winfo_children()[0]
        g.select_card('a')
        g.select_card('c', 4)
        for count in range(3):
            self.event(cover, '<ButtonPress-1>', self.center(cover))
            self.assertIsNone(g.marquee)
            self.assertEqual(g.selected_keys(), ['a', 'c'])
            start = self.center(cover)
            self.event(cover, '<B1-Motion>', (start[0]+30, start[1]+30), 0x100)
            self.event(cover, '<ButtonRelease-1>', (start[0]+30, start[1]+30), 0x100)
            self.assertEqual(g.app.start_native_drag.call_count, count+1)
            self.assertEqual(len(g.app.start_native_drag.call_args.args[0]), 2)
            self.assertEqual(g.selected_keys(), ['a', 'c'])
            self.assert_finished()

    def test_page_switch_and_close_release_timer_grab_and_overlay(self):
        g = self.gallery
        g.PAGE_SIZE = 2
        g._signature = None  # PAGE_SIZE is a fixed product constant, varied only here.
        g.render()
        self.root.update_idletasks()
        self.event(g.grid, '<ButtonPress-1>', self.point(2, 2))
        self.event(g.canvas, '<B1-Motion>', self.point(798, 350), 0x100)
        self.assertEqual(g.selected_keys(), ['a', 'b'])
        g.move(1)
        self.root.update_idletasks()
        self.assertEqual(g.page, 1)
        self.assertEqual(list(g.cards), ['c', 'd'])
        self.assertEqual(g.selected_keys(), [])
        self.assert_finished()
        self.event(g.grid, '<ButtonPress-1>', self.point(2, 2))
        self.event(g.canvas, '<B1-Motion>', self.point(798, 350), 0x100)
        self.outline()
        overlay = g._selection_overlay
        g.stop, g.lock, g.decoders, g.workers = threading.Event(), threading.Lock(), set(), []
        g.embedded = False
        g.timer = g._resize_timer = g._focus_timer = None
        g.app.library_window = g
        g.close()
        self.assertTrue(g.closed)
        self.assertIsNone(g.marquee)
        self.assertIsNone(g._marquee_timer)
        self.assertEqual(g._selection_bindings, [])
        self.assertEqual(g._selection_tag_bindings, [])
        if overlay is not None:
            self.assertFalse(overlay.visible)
            self.assertIsNone(overlay._window)

    def test_actual_action_button_and_name_click_keep_widget_actions(self):
        g = self.gallery
        card = g.cards['a']
        name, actions = card.winfo_children()[1], card.winfo_children()[3]
        command = Mock()
        button = actions.winfo_children()[0]
        button.configure(state='normal', command=command)
        self.event(button, '<ButtonPress-1>', self.center(button))
        self.assertIsNone(g.marquee)
        self.event(button, '<ButtonRelease-1>', self.center(button), 0x100)
        command.assert_called_once()
        self.assert_finished()
        self.event(name, '<ButtonPress-1>', self.center(name))
        self.event(name, '<ButtonRelease-1>', self.center(name), 0x100)
        self.assertEqual(g.selected_keys(), ['a'])
        self.assert_finished()

    def test_native_drag_blocks_body_marquee_and_escape_cancels_it(self):
        g = self.gallery
        g.select_card('c')
        g.app.native_drag_active = True
        card = g.cards['a']
        self.event(card, '<ButtonPress-1>', self.center(card))
        self.assertIsNone(g.marquee)
        self.assertIsNone(g.canvas.grab_current())
        self.assertEqual(g.selected_keys(), ['c'])
        g.escape_selection(SimpleNamespace(widget=g.canvas))
        g.app.cancel_native_drag.assert_called_once()
        self.assertEqual(g.selected_keys(), ['c'])
        self.assert_finished()

    def test_other_grab_prevents_start_and_unpressed_child_motion_recovers(self):
        g = self.gallery
        other = tk.Frame(self.root)
        other.pack()
        other.grab_set()
        self.event(g.grid, '<ButtonPress-1>', self.point(2, 2))
        self.assertIsNone(g.marquee)
        self.assertIs(g.canvas.grab_current(), other)
        other.grab_release()
        self.event(g.grid, '<ButtonPress-1>', self.point(2, 2))
        child = g.cards['a'].winfo_children()[1]
        self.event(child, '<B1-Motion>', self.center(child), 0x100)
        self.event(child, '<Motion>', self.center(child), 0)
        self.assert_finished()


if __name__ == '__main__':
    unittest.main()
