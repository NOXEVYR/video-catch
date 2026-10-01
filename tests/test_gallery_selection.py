"""Selection semantics without Tk; native layout/event tests are separate."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import tkinter as tk

from library_selection import GallerySelection
from library_selection import _pointer_snapshot


class Variable:
    def __init__(self, value=False):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class PointerSnapshotTests(unittest.TestCase):
    def native(self):
        import ctypes
        from ctypes import wintypes
        native = Mock()
        def locate(pointer):
            position = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
            position.x, position.y = -150, 320
            return True
        native.GetCursorPos.side_effect = locate
        native.GetSystemMetrics.return_value = 0
        native.GetAsyncKeyState.return_value = 0x8000
        return native

    def test_negative_coordinates_and_swapped_primary_button(self):
        for swapped, key in ((0,1),(1,2)):
            with self.subTest(swapped=swapped):
                native=self.native()
                native.GetSystemMetrics.return_value=swapped
                with patch('library_selection.sys.platform','win32'), patch('ctypes.windll.user32',native):
                    self.assertEqual(_pointer_snapshot(),(-150,320,True))
                native.GetAsyncKeyState.assert_called_once_with(key)

    def test_unavailable_position_is_unknown_not_zero_or_released(self):
        native=self.native()
        native.GetCursorPos.side_effect=None
        native.GetCursorPos.return_value=False
        with patch('library_selection.sys.platform','win32'), patch('ctypes.windll.user32',native):
            self.assertIsNone(_pointer_snapshot())
        native.GetAsyncKeyState.assert_not_called()

    def test_recent_press_bit_is_not_current_button_down(self):
        native=self.native()
        native.GetAsyncKeyState.return_value=1
        with patch('library_selection.sys.platform','win32'), patch('ctypes.windll.user32',native):
            self.assertEqual(_pointer_snapshot(),(-150,320,False))


class Gallery(GallerySelection):
    def __init__(self):
        self.cards = {key: Mock() for key in 'abcd'}
        self.selection = {key: Variable() for key in self.cards}
        self.canvas = Mock()
        self.anchor_key = None
        self.acting = self.closed = False
        self.marquee = None
        self._marquee_timer = None
        self._selection_edges = [Mock() for _ in range(4)]
        self._selection_bindings = []
        self.window = Mock()
        self.frame = Mock()
    def selected_keys(self):
        return [key for key, variable in self.selection.items() if variable.get() and key in self.cards]


class SelectionSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.gallery = Gallery()
        pointer = patch('library_selection._pointer_snapshot', return_value=None)
        self.snapshot = pointer.start()
        self.addCleanup(pointer.stop)

    def physical_gesture(self):
        g = self.gallery
        g.marquee = {'original': set(), 'start': (10, 10), 'origin': (10, 10),
                     'pointer': (10, 10), 'physical': True, 'dragging': False, 'add': False}
        g._paint_marquee = Mock()
        g.canvas.winfo_rooty.return_value = 0
        g.canvas.winfo_height.return_value = 400
        return g

    def test_real_pointer_overrides_stale_motion_without_ending_held_gesture(self):
        g = self.physical_gesture()
        self.snapshot.return_value = (180, 150, True)
        g.update_marquee(SimpleNamespace(x_root=20, y_root=30, state=0))
        g.recover_pointer_release(SimpleNamespace(state=0))
        self.assertEqual(g.marquee['pointer'], (180, 150))
        self.assertTrue(g.marquee['dragging'])
        g._paint_marquee.assert_called_once()

    def test_physical_tick_tracks_cross_widget_and_finishes_missed_release(self):
        g = self.physical_gesture()
        self.snapshot.return_value = (160, 180, True)
        g._marquee_tick()
        self.assertEqual(g.marquee['pointer'], (160, 180))
        self.assertTrue(g.marquee['dragging'])
        g.canvas.yview_scroll.assert_not_called()
        self.snapshot.return_value = (200, 210, False)
        g._marquee_tick()
        self.assertIsNone(g.marquee)
        self.assertIsNone(g._marquee_timer)
        self.assertEqual(g._paint_marquee.call_count, 2)
        self.assertEqual(g.window.after.call_count, 1)

    def test_unknown_native_pointer_retains_event_driven_gesture(self):
        g = self.physical_gesture()
        g.update_marquee(SimpleNamespace(x_root=70, y_root=80))
        g.recover_pointer_release(SimpleNamespace(state=0x100))
        self.assertEqual(g.marquee['pointer'], (70, 80))
        g._marquee_tick()
        self.assertIsNotNone(g.marquee)

    def test_begin_latches_held_button_including_delayed_press_event(self):
        for snapshot, physical in ((None, False), ((15, 20, False), False),
                                   ((200, 200, True), True), ((15, 20, True), True)):
            with self.subTest(snapshot=snapshot):
                g = Gallery()
                g.canvas.grab_current.return_value = None
                g._point = lambda x,y: (x,y)
                g._marquee_tick = Mock()
                self.snapshot.return_value = snapshot
                g.begin_marquee(SimpleNamespace(x_root=15, y_root=20, state=0))
                self.assertEqual(g.marquee['physical'], physical)

    def test_overlay_failure_reports_in_visible_gallery_without_moving_edges(self):
        g = self.gallery
        g._point = lambda x,y: (x,y)
        g.cards = {}
        g.selection = {}
        g.canvas.canvasx.return_value = g.canvas.canvasy.return_value = 0
        g.canvas.winfo_width.return_value = g.canvas.winfo_height.return_value = 400
        g.marquee = {'start':(10,10), 'pointer':(80,80), 'original':set(), 'add':False}
        g._selection_overlay = Mock(last_error='native failure')
        g._selection_overlay_error_reported = False
        g.status = Mock()
        g._paint_marquee()
        g._paint_marquee()
        g.status.set.assert_called_once()
        for edge in g._selection_edges:
            edge.place.assert_not_called()

    def test_reentrant_show_cancels_outline_and_does_not_schedule_orphan_tick(self):
        g = self.gallery
        g._point = lambda x,y: (x,y)
        g.cards = {}; g.selection = {}
        g.canvas.canvasx.return_value = g.canvas.canvasy.return_value = 0
        g.canvas.winfo_rooty.return_value = 0
        g.canvas.winfo_width.return_value = g.canvas.winfo_height.return_value = 400
        g.marquee = {'start':(10,10), 'pointer':(80,80), 'original':set(), 'add':False, 'dragging':True}
        g._selection_overlay = Mock(last_error=None)
        g._selection_overlay.show.side_effect = lambda _: g.cancel_gesture()
        g._marquee_tick()
        self.assertIsNone(g.marquee)
        self.assertIsNone(g._marquee_timer)
        g._selection_overlay.hide.assert_called()
        g.window.after.assert_not_called()

    def test_plain_click_replaces_and_ctrl_toggles(self):
        g = self.gallery
        g.select_card('a')
        g.select_card('c', 4)
        self.assertEqual(g.selected_keys(), ['a', 'c'])
        g.select_card('a', 4)
        self.assertEqual(g.selected_keys(), ['c'])
        g.select_card('b')
        self.assertEqual(g.selected_keys(), ['b'])

    def test_shift_range_and_ctrl_shift_union(self):
        g = self.gallery
        g.select_card('a')
        g.select_card('c', 1)
        self.assertEqual(g.selected_keys(), ['a', 'b', 'c'])
        g.select_card('d', 4)
        g.select_card('c', 5)
        self.assertEqual(g.selected_keys(), list('abcd'))

    def test_cover_press_preserves_group_until_release(self):
        g = self.gallery
        g.select_card('a')
        g.select_card('c', 4)
        g.select_card('a', preserve_group=True)
        self.assertEqual(g.selected_keys(), ['a', 'c'])
        g.drag_start = (0, 0, 'a')
        g.release_cover(SimpleNamespace(state=0))
        self.assertEqual(g.selected_keys(), ['a'])

    def test_real_drag_clears_click_collapse(self):
        g = self.gallery
        g.select_card('a')
        g.select_card('c', 4)
        g.drag_start = None
        g.release_cover(SimpleNamespace(state=0))
        self.assertEqual(g.selected_keys(), ['a', 'c'])

    def test_cancel_marquee_restores_snapshot_and_releases_capture(self):
        g = self.gallery
        g.select_card('b')
        g.marquee = {'original': {'a', 'd'}}
        g._marquee_timer = 'timer'
        g.canvas.grab_current.return_value = g.canvas
        g.end_marquee(cancel=True)
        self.assertEqual(g.selected_keys(), ['a', 'd'])
        g.canvas.grab_release.assert_called_once()
        g.window.after_cancel.assert_called_once_with('timer')
        self.assertIsNone(g.marquee)
        self.assertIsNone(g._marquee_timer)

    def test_cancel_mixed_gesture_restores_batch_state_and_is_idempotent(self):
        g = self.gallery
        g.selection_caption = Variable()
        g.batch_buttons = [Mock()]
        g.select_card('b')
        g.drag_start = (0, 0, 'b')
        g.marquee = {'original': {'a', 'd'}}
        g._marquee_timer = 'timer'
        g.canvas.grab_current.return_value = g.canvas
        g.canvas.grab_release.side_effect = lambda: setattr(g.canvas.grab_current, 'return_value', None)
        g.cancel_gesture(cancel_marquee=True)
        g.cancel_gesture(cancel_marquee=True)
        self.assertIsNone(g.drag_start)
        self.assertIsNone(g.marquee)
        self.assertIsNone(g._marquee_timer)
        self.assertEqual(g.selected_keys(), ['a', 'd'])
        self.assertEqual(g.selection_caption.get(), '已选 2 项')
        g.batch_buttons[0].configure.assert_called_with(state='normal')
        g.window.after_cancel.assert_called_once_with('timer')
        g.canvas.grab_release.assert_called_once()

    def test_end_without_gesture_cleans_orphan_timer_capture_and_edges(self):
        g = self.gallery
        g._marquee_timer = 'orphan'
        g.window.after_cancel.side_effect = tk.TclError('window gone')
        g.canvas.grab_current.return_value = g.canvas
        g._selection_edges[0].place_forget.side_effect = tk.TclError('edge gone')
        g.end_marquee()
        self.assertIsNone(g._marquee_timer)
        g.canvas.grab_release.assert_called_once()
        for edge in g._selection_edges:
            edge.place_forget.assert_called_once()

    def test_unpressed_motion_clears_pending_drag_without_marquee(self):
        g = self.gallery
        g.select_card('a')
        g.drag_start = (10, 10, 'a')
        g.recover_pointer_release(SimpleNamespace(state=0))
        self.assertIsNone(g.drag_start)
        self.assertEqual(g.selected_keys(), ['a'])

    def test_partial_initialization_without_canvas_or_window_cleans_idempotently(self):
        g = GallerySelection.__new__(GallerySelection)
        g.drag_start = (10, 10, 'a')
        g._marquee_timer = 'orphan'
        g._selection_edges = [Mock() for _ in range(4)]
        g.cancel_gesture(cancel_marquee=True)
        g.teardown_selection()
        self.assertIsNone(g.drag_start)
        self.assertIsNone(g.marquee)
        self.assertIsNone(g._marquee_timer)
        self.assertEqual(g._selection_bindings, [])
        for edge in g._selection_edges:
            self.assertEqual(edge.place_forget.call_count, 2)

    def test_begin_marquee_cancels_pending_drag_and_preserves_snapshot(self):
        g = self.gallery
        g.select_card('a')
        g.drag_start = (10, 10, 'a')
        g.canvas.grab_current.return_value = None
        g._point = lambda x, y: (x, y)
        g._marquee_tick = Mock()
        g.begin_marquee(SimpleNamespace(x_root=15, y_root=20, state=0))
        self.assertIsNone(g.drag_start)
        self.assertEqual(g.marquee['original'], {'a'})
        self.assertEqual(g.selected_keys(), [])
        g.canvas.grab_set.assert_called_once()
        g.cancel_gesture(cancel_marquee=True)
        self.assertEqual(g.selected_keys(), ['a'])

    def test_native_drag_rejects_marquee_without_changing_selection(self):
        g = self.gallery
        g.select_card('a')
        g.app = SimpleNamespace(native_drag_active=True)
        g.drag_start = (10, 10, 'a')
        g._marquee_tick = Mock()
        g.begin_marquee(SimpleNamespace(x_root=15, y_root=20, state=0))
        self.assertIsNone(g.drag_start)
        self.assertIsNone(g.marquee)
        self.assertEqual(g.selected_keys(), ['a'])
        g.canvas.grab_set.assert_not_called()
        g._marquee_tick.assert_not_called()

    def test_cover_then_global_release_preserves_completed_drag_group(self):
        g = self.gallery
        g.select_card('a')
        g.select_card('c', 4)
        g.drag_start = None  # The native drag already consumed the pending click.
        event = SimpleNamespace(state=0)
        g.release_cover(event)
        self.assertIsNone(g.selection_pointer_release(event))
        self.assertEqual(g.selected_keys(), ['a', 'c'])

    def test_global_release_cleans_pending_without_collapsing_group(self):
        g = self.gallery
        g.select_card('a')
        g.select_card('c', 4)
        g.drag_start = (10, 10, 'a')
        g.selection_pointer_release(SimpleNamespace(state=0))
        self.assertIsNone(g.drag_start)
        self.assertEqual(g.selected_keys(), ['a', 'c'])

    def test_focus_loss_only_clears_gesture_but_hide_cancels_native_drag(self):
        for lifecycle in ('focus', 'hide'):
            with self.subTest(lifecycle=lifecycle):
                g = Gallery()
                cancel = Mock()
                g.app = SimpleNamespace(native_drag_active=True, cancel_native_drag=cancel)
                g.drag_start = (10, 10, 'a')
                if lifecycle == 'focus':
                    g.window.focus_displayof.return_value = None
                    g.selection_focus_out(SimpleNamespace(widget=g.canvas))
                else:
                    g.selection_unmapped(SimpleNamespace(widget=g.frame))
                if lifecycle == 'focus':
                    cancel.assert_not_called()
                else:
                    cancel.assert_called_once()
                self.assertIsNone(g.drag_start)
                self.assertIsNone(g.marquee)

    def test_escape_native_cancel_preserves_selection_and_optional_hook(self):
        g = self.gallery
        g.select_card('a')
        cancel = Mock()
        g.app = SimpleNamespace(native_drag_active=True, cancel_native_drag=cancel)
        self.assertEqual(g.escape_selection(SimpleNamespace(widget=g.canvas)), 'break')
        cancel.assert_called_once()
        self.assertEqual(g.selected_keys(), ['a'])
        g.app = SimpleNamespace(native_drag_active=True)
        self.assertEqual(g.escape_selection(SimpleNamespace(widget=g.canvas)), 'break')
        self.assertEqual(g.selected_keys(), ['a'])

    def test_native_tick_cancels_old_marquee_and_does_not_reschedule(self):
        g = self.gallery
        g.select_card('b')
        g.marquee = {'original': {'a'}}
        g.app = SimpleNamespace(native_drag_active=True)
        g._marquee_tick()
        self.assertIsNone(g.marquee)
        self.assertEqual(g.selected_keys(), ['a'])
        g.window.after.assert_not_called()

    def test_native_selection_shortcuts_preserve_dragged_group(self):
        g = self.gallery
        g.select_card('a')
        g.select_card('c', 4)
        g.app = SimpleNamespace(native_drag_active=True)
        g.select_page = Mock()
        g.drag_start = (10, 10, 'a')
        self.assertEqual(g.select_card('b'), 'break')
        self.assertEqual(g.clear_selection(), 'break')
        self.assertEqual(g._select_all_key(), 'break')
        self.assertEqual(g.selected_keys(), ['a', 'c'])
        self.assertIsNone(g.drag_start)
        g.select_page.assert_not_called()

    def test_selecting_card_cancels_active_marquee_before_new_click(self):
        g = self.gallery
        g.marquee = {'original': {'a'}}
        g.drag_start = (0, 0, 'b')
        g.select_card('c', 4)
        self.assertEqual(g.selected_keys(), ['a', 'c'])
        self.assertIsNone(g.marquee)
        self.assertIsNone(g.drag_start)

    def test_batch_feedback_is_disabled_for_empty_or_busy_selection(self):
        g = self.gallery
        g.selection_caption = Variable()
        g.batch_buttons = [Mock(), Mock()]
        g.selection_changed()
        g.batch_buttons[0].configure.assert_called_with(state='disabled')
        g.select_card('a')
        self.assertEqual(g.selection_caption.get(), '已选 1 项')
        g.batch_buttons[0].configure.assert_called_with(state='normal')
        g.acting = True
        g.selection_changed()
        g.batch_buttons[0].configure.assert_called_with(state='disabled')
        g.acting = False
        g.app = SimpleNamespace(native_drag_active=True)
        g.selection_changed()
        g.batch_buttons[0].configure.assert_called_with(state='disabled')
        g.app.native_drag_active = False
        g.selection_changed()
        g.batch_buttons[0].configure.assert_called_with(state='normal')

    def test_escape_does_not_clear_selection_while_typing_elsewhere(self):
        g = self.gallery
        g.select_card('a')
        g.escape_selection(SimpleNamespace(widget=Mock()))
        self.assertEqual(g.selected_keys(), ['a'])
        g.escape_selection(SimpleNamespace(widget=g.canvas))
        self.assertEqual(g.selected_keys(), [])

    def test_reverse_marquee_uses_intersection_and_ctrl_adds_snapshot(self):
        g = self.gallery
        g._point = lambda x, y: (x, y)
        g.canvas.canvasx.return_value = g.canvas.canvasy.return_value = 0
        g.canvas.winfo_width.return_value = g.canvas.winfo_height.return_value = 400
        for index, card in enumerate(g.cards.values()):
            card.winfo_rootx.return_value = (index % 2) * 110
            card.winfo_rooty.return_value = (index // 2) * 100
            card.winfo_width.return_value = 100
            card.winfo_height.return_value = 80
        g.marquee = {'start': (215, 85), 'pointer': (5, 5), 'original': {'d'}, 'add': False}
        g._paint_marquee()
        self.assertEqual(g.selected_keys(), ['a', 'b'])
        g.marquee['add'] = True
        g._paint_marquee()
        self.assertEqual(g.selected_keys(), ['a', 'b', 'd'])

    def test_motion_at_edge_does_not_scroll_until_drag_threshold(self):
        g = self.gallery
        g.canvas.winfo_rooty.return_value = 0
        g.canvas.winfo_height.return_value = 400
        g._paint_marquee = Mock()
        g.marquee = {'pointer': (10, 399), 'dragging': False}
        g._marquee_tick()
        g.canvas.yview_scroll.assert_not_called()
        g.marquee['dragging'] = True
        g._marquee_tick()
        g.canvas.yview_scroll.assert_called_once_with(1, 'units')


if __name__ == '__main__':
    unittest.main()
