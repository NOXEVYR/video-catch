from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


class RecordingControlStackTests(unittest.TestCase):
    def test_annotation_moves_below_actual_decorated_transport(self):
        transport, annotation = MagicMock(), MagicMock()
        app = SimpleNamespace(recording_toolbar=SimpleNamespace(window=transport),
                              annotations=SimpleNamespace(toolbar=annotation))
        with patch("capture_native._tk_hwnd", side_effect=[100, 200]), \
                patch("capture_native.window_info", side_effect=[
                    dict(x=300, y=24, width=900, height=116),
                    dict(x=400, y=90, width=700, height=70)]), \
                patch("capture_native.place_window") as place:
            App._raise_recording_controls(app)
            place.assert_called_once_with(annotation, dict(x=400, y=152, width=700, height=70))
        transport.lift.assert_called_once()

    def test_already_clear_annotation_is_not_moved(self):
        transport, annotation = MagicMock(), MagicMock()
        app = SimpleNamespace(recording_toolbar=SimpleNamespace(window=transport),
                              annotations=SimpleNamespace(toolbar=annotation))
        with patch("capture_native._tk_hwnd", side_effect=[100, 200]), \
                patch("capture_native.window_info", side_effect=[
                    dict(x=-1600, y=-200, width=900, height=116),
                    dict(x=-1600, y=20, width=700, height=70)]), \
                patch("capture_native.place_window") as place:
            App._raise_recording_controls(app)
            place.assert_not_called()
        transport.lift.assert_called_once()

    def test_absent_annotation_still_raises_transport(self):
        transport = MagicMock()
        app = SimpleNamespace(recording_toolbar=SimpleNamespace(window=transport), annotations=None)
        App._raise_recording_controls(app)
        transport.lift.assert_called_once()


if __name__ == "__main__":
    unittest.main()
