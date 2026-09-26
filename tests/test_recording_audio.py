"""Device selection and bounded lifecycle tests; never open real hardware."""
import queue
import unittest
from unittest.mock import Mock, patch

from recording_audio import AudioCapture, _inventory, _select, _write_timed_audio


class AudioTests(unittest.TestCase):
    def test_midstream_silence_is_preserved_using_adc_clock(self):
        output = Mock()
        timing = {"sample_rate": 1000, "channels": 1, "frames": 0, "started": None}
        _write_timed_audio(output, timing, b"a" * 200, 100, {"input_buffer_adc_time": 50.}, 100.)
        _write_timed_audio(output, timing, b"b" * 200, 100, {"input_buffer_adc_time": 50.8}, 100.9)
        self.assertAlmostEqual(timing["started"], 99.9)
        self.assertEqual(timing["frames"], 900)
        self.assertEqual([c.args[0] for c in output.writeframesraw.call_args_list],
                         [b"a" * 200, bytes(1400), b"b" * 200])

    def test_missing_adc_clock_uses_monotonic_silence(self):
        output = Mock()
        timing = {"sample_rate": 1000, "channels": 1, "frames": 0, "started": None}
        _write_timed_audio(output, timing, b"a" * 200, 100, {}, 100.)
        _write_timed_audio(output, timing, b"b" * 200, 100, {}, 100.8)
        self.assertEqual(timing["frames"], 900)

    def test_explicit_missing_device_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, "已不可用"):
            _select([{"index": 3}], 99, "麦克风")

    def test_default_selection_and_missing_device(self):
        default = {"index": 3, "default": True}
        self.assertEqual(_select([{"index": 1}, default], None, "麦克风"), default)
        with self.assertRaisesRegex(ValueError, "未找到"):
            _select([], None, "系统声音设备")

    def test_inventory_only_includes_wasapi_input_endpoints(self):
        pa = Mock()
        pa.get_host_api_info_by_type.return_value = {"index": 2, "defaultInputDevice": 0}
        pa.get_default_wasapi_loopback.return_value = {"index": 1}
        devices = [
            {"hostApi": 2, "name": "Mic", "maxInputChannels": 1, "defaultSampleRate": 48000},
            {"hostApi": 2, "name": "Speakers loopback", "maxInputChannels": 2,
             "defaultSampleRate": 48000, "isLoopbackDevice": True},
            {"hostApi": 1, "name": "MME duplicate", "maxInputChannels": 1},
            {"hostApi": 2, "name": "Output", "maxInputChannels": 0},
        ]
        pa.get_device_count.return_value = len(devices)
        pa.get_device_info_by_index.side_effect = devices
        with patch("recording_audio._module"):
            result = _inventory(pa)
        self.assertEqual([d["name"] for d in result["microphones"]], ["Mic"])
        self.assertEqual([d["name"] for d in result["systems"]], ["Speakers loopback"])
        self.assertTrue(result["microphones"][0]["default"])
        self.assertTrue(result["systems"][0]["default"])
        pa.open.assert_not_called()

    def test_driver_stop_is_bounded_and_idempotent(self):
        with patch("recording_audio.mp.get_context") as context:
            capture = AudioCapture({"audio": "system"}, "unused")
        process = capture._process
        process.pid = 1
        process.is_alive.return_value = True
        capture._messages.get_nowait.side_effect = queue.Empty
        capture.stop()
        process.terminate.assert_called_once()
        self.assertEqual([c.args[0] for c in process.join.call_args_list], [5, 3])
        self.assertIn("未及时停止", capture.error)
        capture.stop()
        process.terminate.assert_called_once()

    def test_unexpected_audio_exit_cannot_silently_drop_requested_sound(self):
        with patch("recording_audio.mp.get_context"):
            capture = AudioCapture({"audio": "both"}, "unused")
        capture._process.pid = 1
        capture._process.is_alive.return_value = False
        capture._messages.get_nowait.side_effect = queue.Empty
        capture.stop()
        self.assertIn("未完整结束", capture.error)

    def test_partial_audio_completion_is_failure(self):
        with patch("recording_audio.mp.get_context"):
            capture = AudioCapture({"audio": "both"}, "unused")
        capture._process.pid = 1
        capture._process.is_alive.return_value = False
        capture._messages.get_nowait.side_effect = [{"tracks": [{"path": "one.wav"}]}, queue.Empty]
        capture.stop()
        self.assertIn("未完整结束", capture.error)

    def test_successful_audio_completion_requires_all_requested_tracks(self):
        with patch("recording_audio.mp.get_context"):
            capture = AudioCapture({"audio": "both"}, "unused")
        capture._process.pid = 1
        capture._process.is_alive.return_value = False
        tracks = [{"path": "one.wav"}, {"path": "two.wav"}]
        capture._messages.get_nowait.side_effect = [{"tracks": tracks}, queue.Empty]
        self.assertEqual(capture.stop(), tracks)
        self.assertEqual(capture.error, "")


if __name__ == "__main__":
    unittest.main()
