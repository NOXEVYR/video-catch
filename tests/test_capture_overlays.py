from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture_overlays import AnnotationOverlay, RegionSelector, region_from_points


class RegionTests(unittest.TestCase):
    bounds = dict(x=-1920, y=-200, width=4480, height=1640)

    def test_reverse_drag_crossing_negative_monitor_origin(self):
        self.assertEqual(region_from_points((500, 800), (-1800, -100), self.bounds),
                         dict(x=-1800, y=-100, width=2300, height=900))

    def test_drag_clamps_to_desktop(self):
        self.assertEqual(region_from_points((-9999, -9999), (9999, 9999), self.bounds), self.bounds)

    def test_click_or_one_pixel_selection_cancels(self):
        self.assertIsNone(region_from_points((5, 5), (5, 5), self.bounds))
        self.assertIsNone(region_from_points((5, 5), (6, 100), self.bounds))

    def test_selector_destroys_before_callback_and_only_calls_once(self):
        selector = RegionSelector.__new__(RegionSelector)
        selector.root = MagicMock()
        selector.window = MagicMock()
        selector.active = True
        callback = MagicMock()
        selector.on_selected = callback
        selector._finish(self.bounds)
        selector.close()
        self.assertFalse(selector.active)
        selector.window.destroy.assert_called_once()
        callback.assert_not_called()
        selector.root.after.assert_called_once()
        selector.root.after.call_args.args[1]()
        callback.assert_called_once_with(self.bounds)


class InkTests(unittest.TestCase):
    def setUp(self):
        platform = patch("capture_overlays.sys.platform", "win32")
        platform.start()
        self.addCleanup(platform.stop)
        self.ink = AnnotationOverlay(MagicMock())
        self.ink.bounds = dict(x=-1920, y=-100, width=4480, height=1540)
        self.ink.canvas = MagicMock()
        self.ink.canvas.create_oval.return_value = 10
        self.ink.canvas.create_line.side_effect = [11, 12]

    def test_stroke_undo_removes_whole_gesture(self):
        self.ink._press(SimpleNamespace(x_root=-1900, y_root=-80))
        self.ink._drag(SimpleNamespace(x_root=-1800, y_root=20))
        self.ink._drag(SimpleNamespace(x_root=-1700, y_root=40))
        self.ink._release(None)
        self.assertEqual(self.ink._strokes, [[10, 11, 12]])
        self.assertEqual(self.ink.canvas.create_line.call_args_list[0].args, (20, 20, 120, 120))
        self.ink.undo()
        self.assertEqual([call.args[0] for call in self.ink.canvas.delete.call_args_list], [10, 11, 12])
        self.assertEqual(self.ink._strokes, [])

    def test_finish_preserves_ink_and_removes_input_surface(self):
        self.ink.input_window = MagicMock()
        catcher = self.ink.input_window
        self.ink.drawing = True
        self.ink.active = True
        self.ink._drawing_button = MagicMock()
        self.ink.finish()
        self.assertTrue(self.ink.active)
        self.assertFalse(self.ink.drawing)
        self.assertIsNone(self.ink.input_window)
        catcher.destroy.assert_called_once()
        self.ink.canvas.delete.assert_not_called()

    def test_close_destroys_all_native_surfaces(self):
        windows = [MagicMock(), MagicMock(), MagicMock()]
        self.ink.input_window, self.ink.toolbar, self.ink.window = windows
        self.ink.active = self.ink.drawing = True
        self.ink.close()
        self.ink.close()
        for window in windows:
            window.destroy.assert_called_once()
        self.assertFalse(self.ink.active)
        self.assertFalse(self.ink.drawing)

    def test_input_ready_callback_runs_after_surfaces_lift_and_each_resume(self):
        order = []
        self.ink.on_input_ready = lambda: order.append("ready")
        self.ink.active = True
        self.ink.window = MagicMock()
        self.ink.window.lift.side_effect = lambda: order.append("ink")
        self.ink.toolbar = MagicMock()
        self.ink.toolbar.lift.side_effect = lambda: order.append("toolbar")
        self.ink._drawing_button = MagicMock()
        with patch("capture_overlays.tk.Toplevel"), patch("capture_overlays.place_window"), \
                patch("capture_overlays.exclude_from_capture"):
            self.ink._start_drawing()
            self.assertEqual(order, ["ink", "toolbar", "ready"])
            self.assertTrue(self.ink.drawing)
            self.ink._start_drawing()
            self.assertEqual(len(order), 3)
            self.ink.finish()
            self.ink._start_drawing()
            self.assertEqual(order, ["ink", "toolbar", "ready"] * 2)
            self.ink.close()
            self.ink._start_drawing()
            self.assertEqual(len(order), 6)


if __name__ == "__main__":
    unittest.main()
