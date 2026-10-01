"""Synthetic Tk/Win32 lifecycle checks; no real pointer/device interaction."""
import ctypes as C
from ctypes import wintypes as W
import gc
from pathlib import Path
import sys
import threading
import tkinter as tk
import unittest
import weakref
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import selection_overlay as native


class OverlayPlatformTests(unittest.TestCase):
    def test_non_windows_is_an_inert_fallback(self):
        owner = Mock()
        with patch.object(native.sys, 'platform', 'darwin'):
            overlay = native.SelectionOverlay(owner, '#9B80D1')
        self.assertFalse(overlay.supported)
        self.assertFalse(overlay.show((1, 2, 30, 40)))
        overlay.hide()
        overlay.close()
        overlay.close()
        self.assertFalse(overlay.visible)
        owner.assert_not_called()
        self.assertEqual(owner.mock_calls, [])

    def test_wrong_thread_rejected_before_any_tk_call(self):
        owner = Mock()
        with patch.object(native.sys, 'platform', 'darwin'):
            overlay = native.SelectionOverlay(owner, '#9B80D1')
        errors = []
        def invoke():
            for method in (lambda: overlay.show((1, 2, 30, 40)), overlay.hide, overlay.close):
                try:
                    method()
                except RuntimeError as error:
                    errors.append(str(error))
        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join()
        self.assertEqual(len(errors), 3)
        self.assertEqual(owner.mock_calls, [])


@unittest.skipUnless(sys.platform == 'win32', 'Windows native overlay')
class OverlayTkTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk unavailable: {error}')
        # Create only our synthetic fixture, positioned outside the desktop.
        self.root.geometry('320x240+12000+12000')
        self.owner = tk.Canvas(self.root, borderwidth=0, highlightthickness=0)
        self.owner.pack(fill='both', expand=True)
        self.root.update()
        self.overlay = native.SelectionOverlay(self.owner, '#9B80D1')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        # Tk.destroy releases native windows, not every Python/Tcl reference.
        # Mock wraps/exception tracebacks can retain widget cycles until GC;
        # release our fixture and collect here on its interpreter's UI thread,
        # before later HTTP tests can trigger a collection on a server thread.
        try:
            if self.overlay is not None:
                self.overlay.close()
            if self.root is not None:
                try:
                    self.root.destroy()
                except tk.TclError:
                    pass
        finally:
            self.overlay = self.owner = self.root = None
            gc.collect()

    def show(self, bounds=(10, 20, 120, 100)):
        self.assertTrue(self.overlay.show(bounds), self.overlay.last_error)
        self.root.update_idletasks()

    def native_rect(self):
        u = native._api()
        u.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        u.GetWindowRect.restype = W.BOOL
        rect = W.RECT()
        self.assertTrue(u.GetWindowRect(self.overlay._hwnd, C.byref(rect)))
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

    def test_lazy_fixed_geometry_and_single_rectangle(self):
        o = self.overlay
        self.assertIsNone(o._window)
        self.show()
        window, canvas, item = o._window, o._canvas, o._rectangle
        expected = (self.owner.winfo_rootx(), self.owner.winfo_rooty(),
                    self.owner.winfo_width(), self.owner.winfo_height())
        self.assertEqual(self.native_rect(), expected)
        with patch.object(window, 'geometry', wraps=window.geometry) as geometry, \
                patch.object(o, '_position', wraps=o._position) as position:
            for index in range(60):
                self.show((10, 20, 120 + index, 100 + index))
            geometry.assert_not_called()
            position.assert_not_called()
        self.assertIs(o._window, window)
        self.assertEqual(canvas.find_all(), (item,))
        self.assertEqual(canvas.coords(item), [10.0, 20.0, 179.0, 159.0])
        self.assertEqual(window.winfo_children(), [canvas])
        self.assertEqual(self.native_rect(), expected)

    def test_normalizes_clips_and_hides_empty_boxes(self):
        self.show((900, 700, -20, -30))
        o = self.overlay
        self.assertEqual(o._canvas.coords(o._rectangle),
                         [0.0, 0.0, float(self.owner.winfo_width()), float(self.owner.winfo_height())])
        self.assertEqual(o._canvas.itemcget(o._rectangle, 'fill'), '')
        for bounds in ((10, 10, 12, 100), (-20, 10, -10, 100), (10, 500, 40, 600)):
            self.assertFalse(o.show(bounds))
            self.assertFalse(o.visible)
            self.assertEqual(o._window.state(), 'withdrawn')

    def test_styles_focus_grab_and_non_topmost(self):
        u = native._api()
        u.GetForegroundWindow.argtypes, u.GetForegroundWindow.restype = [], W.HWND
        foreground = u.GetForegroundWindow()
        before_focus = self.root.focus_displayof()
        before_grab = self.root.grab_current()
        self.show()
        self.root.update()
        self.assertIs(u.GetAncestor.restype, W.HWND)
        self.assertEqual(C.sizeof(u._get_style.restype), C.sizeof(C.c_void_p))
        self.assertEqual(C.sizeof(u._set_style.restype), C.sizeof(C.c_void_p))
        self.assertEqual(u._get_style(self.overlay._hwnd, -20) & native._EX_STYLE, native._EX_STYLE)
        self.assertEqual(u._get_style(self.overlay._hwnd, -20) & 0x00000008, 0)  # WS_EX_TOPMOST
        self.assertFalse(self.overlay._window.attributes('-topmost'))
        self.assertEqual(self.root.focus_displayof(), before_focus)
        self.assertEqual(self.root.grab_current(), before_grab)
        self.assertEqual(u.GetForegroundWindow(), foreground)

    def test_resize_and_absolute_negative_position(self):
        self.show()
        self.root.geometry('400x280+12000+12000')
        self.root.update()
        self.show()
        self.assertEqual(self.native_rect(), (self.owner.winfo_rootx(), self.owner.winfo_rooty(), 400, 280))
        with patch.object(self.owner, 'winfo_rootx', return_value=-700), \
                patch.object(self.owner, 'winfo_rooty', return_value=-500):
            self.show()
            self.root.update()
        self.assertEqual(self.native_rect(), (-700, -500, 400, 280))

    def test_hide_reshow_close_no_accumulating_windows_or_timers(self):
        before_timers = self.root.tk.call('after', 'info')
        self.show()
        window = self.overlay._window
        for _ in range(20):
            self.overlay.hide()
            self.overlay.hide()
            self.show()
        self.assertIs(self.overlay._window, window)
        self.assertEqual(len(self.root.winfo_children()), 2)
        self.assertEqual(self.root.tk.call('after', 'info'), before_timers)
        self.overlay.close()
        self.overlay.close()
        self.assertEqual(self.root.winfo_children(), [self.owner])
        self.assertFalse(self.overlay.show((10, 20, 30, 40)))
        self.assertEqual(self.owner.bind('<Destroy>'), '')
        self.assertEqual(self.owner.bind('<Unmap>'), '')

    def test_owner_unmap_and_destroy_do_not_recreate_overlay(self):
        self.show()
        self.owner.pack_forget()
        self.root.update()
        self.assertFalse(self.overlay.visible)
        self.assertFalse(self.overlay.show((10, 20, 30, 40)))
        self.owner.pack(fill='both', expand=True)
        self.root.update()
        self.show()
        self.root.destroy()
        self.assertFalse(self.overlay.visible)
        self.overlay.hide()
        self.overlay.close()
        self.assertFalse(self.overlay.show((10, 20, 30, 40)))
        self.assertIsNone(self.overlay._window)

    def test_native_failure_cleans_partial_window_and_disables_fallback(self):
        with patch.object(self.overlay, '_styles', side_effect=OSError('synthetic native failure')):
            self.assertFalse(self.overlay.show((10, 20, 100, 120)))
        self.assertFalse(self.overlay.supported)
        self.assertFalse(self.overlay.visible)
        self.assertEqual(self.overlay.last_error, 'synthetic native failure')
        self.assertEqual(self.root.winfo_children(), [self.owner])
        self.assertFalse(self.overlay.show((10, 20, 100, 120)))

    def test_draw_failure_releases_an_already_visible_overlay(self):
        self.show()
        with patch.object(self.overlay._canvas, 'coords', side_effect=tk.TclError('synthetic draw failure')):
            self.assertFalse(self.overlay.show((20, 30, 120, 140)))
        self.assertFalse(self.overlay.supported)
        self.assertFalse(self.overlay.visible)
        self.assertEqual(self.root.winfo_children(), [self.owner])
        self.assertIsNone(self.overlay._window)

    def test_close_preserves_other_owner_bindings(self):
        token = self.owner.bind('<Unmap>', lambda event: None, add='+')
        self.show()
        self.overlay.close()
        self.assertIn(token, self.owner.bind('<Unmap>'))
        self.owner.unbind('<Unmap>', token)

    def test_invalid_bounds_do_not_create_resources(self):
        for bounds in ((1, 2, 3), (1, 2, 3, 4, 5), (1, 2, float('nan'), 100),
                       (1, 2, float('inf'), 100), None):
            with self.assertRaises(ValueError):
                self.overlay.show(bounds)
        self.assertIsNone(self.overlay._window)
        self.assertTrue(self.overlay.supported)

    def test_tk_failure_and_destroyed_root_before_first_show_are_safe(self):
        with patch.object(tk.Toplevel, 'attributes', side_effect=tk.TclError('synthetic Tk failure')):
            self.assertFalse(self.overlay.show((10, 20, 100, 120)))
        self.assertFalse(self.overlay.supported)
        self.assertEqual(self.root.winfo_children(), [self.owner])
        other = native.SelectionOverlay(self.owner, '#9B80D1')
        self.root.destroy()
        self.assertFalse(other.show((10, 20, 100, 120)))
        other.hide()
        other.close()

    def test_idle_owner_withdraw_during_creation_does_not_remap_overlay(self):
        self.root.after_idle(self.root.withdraw)
        self.assertFalse(self.overlay.show((10, 20, 100, 120)))
        self.assertFalse(self.overlay.visible)
        self.assertEqual(self.root.state(), 'withdrawn')
        if self.overlay._window is not None:
            self.assertEqual(self.overlay._window.state(), 'withdrawn')
        self.assertTrue(self.overlay.supported)
        self.root.deiconify()
        self.root.update()
        self.show()  # A new gesture can show after an ordinary owner hide.

    def test_idle_owner_destroy_during_creation_does_not_revive_resources(self):
        self.root.after_idle(self.root.destroy)
        self.assertFalse(self.overlay.show((10, 20, 100, 120)))
        self.assertFalse(self.overlay.visible)
        self.assertIsNone(self.overlay._window)
        self.assertIsNone(self.overlay.last_error)
        self.assertFalse(self.overlay.show((10, 20, 100, 120)))

    def test_idle_overlay_hide_or_close_cancels_inflight_show(self):
        self.root.after_idle(self.overlay.hide)
        self.assertFalse(self.overlay.show((10, 20, 100, 120)))
        self.assertFalse(self.overlay.visible)
        self.assertEqual(self.overlay._window.state(), 'withdrawn')
        self.overlay.close()
        self.overlay = native.SelectionOverlay(self.owner, '#9B80D1')
        self.root.after_idle(self.overlay.close)
        self.assertFalse(self.overlay.show((10, 20, 100, 120)))
        self.assertIsNone(self.overlay._window)
        self.assertEqual(self.root.winfo_children(), [self.owner])
        self.assertIsNone(self.overlay.last_error)

    def test_idle_native_window_destroy_during_creation_is_safe(self):
        self.root.after_idle(lambda: self.overlay._window.destroy())
        self.assertFalse(self.overlay.show((10, 20, 100, 120)))
        self.assertFalse(self.overlay.visible)
        self.assertIsNone(self.overlay._window)
        self.assertFalse(self.overlay.show((10, 20, 100, 120)))
        self.assertEqual(self.root.winfo_children(), [self.owner])

    def test_cleanup_releases_mock_cycles_and_destroyed_tk_fixture(self):
        self.show()
        root_ref = weakref.ref(self.root)
        owner_ref = weakref.ref(self.owner)
        window_ref = weakref.ref(self.overlay._window)
        # This is the same bound-method wrapping used by the fixed-geometry
        # test. Mock's internal call state may form cycles holding the window.
        with patch.object(self.overlay._window, 'geometry',
                          wraps=self.overlay._window.geometry):
            self.overlay._window.geometry('320x240')
        self.cleanup()
        self.assertIsNone(root_ref())
        self.assertIsNone(owner_ref())
        self.assertIsNone(window_ref())
        self.cleanup()  # unittest's registered cleanup must remain idempotent.


if __name__ == '__main__':
    unittest.main()
