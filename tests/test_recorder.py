"""Recording tests use lavfi and generated WAVs, never screen/mic/camera input."""
from pathlib import Path
import subprocess
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import recorder
import recording_audio_macos as mac_capture


MAC_DEVICES = """[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] [1] Capture screen 0
[AVFoundation indev @ 0x1] [2] Capture screen 1
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Microphone
[AVFoundation indev @ 0x1] [1] BlackHole 2ch
"""


class MacPlanningTests(unittest.TestCase):
    def setUp(self):
        self.inventory = mac_capture.parse_devices(MAC_DEVICES)
        self.monitors = [
            {"id": 1, "display_index": 0, "primary": True, "x": 0, "y": 0,
             "width": 1440, "height": 900, "pixel_width": 2880, "pixel_height": 1800},
            {"id": 2, "display_index": 1, "primary": False, "x": -1920, "y": -100,
             "width": 1920, "height": 1080, "pixel_width": 1920, "pixel_height": 1080},
        ]

    def test_inventory_separates_screen_camera_mic_and_virtual_audio(self):
        self.assertEqual(self.inventory["cameras"], [{"index": 0, "name": "FaceTime HD Camera"}])
        self.assertEqual([d["display_index"] for d in self.inventory["screens"]], [0, 1])
        self.assertEqual([d["name"] for d in self.inventory["systems"]], ["BlackHole 2ch"])
        self.assertEqual([d["name"] for d in self.inventory["microphones"]], ["MacBook Microphone"])

    def test_default_primary_screen_retina_size(self):
        geometry = mac_capture.capture_geometry(recorder.normalize_options({}), self.monitors)
        self.assertEqual((geometry["width"], geometry["height"]), (2880, 1800))
        self.assertEqual(geometry["display_index"], 0)

    def test_negative_origin_region_maps_to_correct_display_and_crop(self):
        options = recorder.normalize_options({"mode": "region", "region": {"x": -1800, "y": -50,
                                                                                 "width": 500, "height": 300}})
        geometry = mac_capture.capture_geometry(options, self.monitors)
        self.assertEqual(geometry["crop"], {"x": 120, "y": 50, "width": 500, "height": 300})
        plan = mac_capture.capture_plan(options, geometry, self.inventory)
        self.assertEqual(plan["inputs"][-1], "2:none")

    def test_retina_window_crop_uses_client_logical_rect(self):
        options = recorder.normalize_options({"mode": "window", "hwnd": 10})
        window = {"x": 100, "y": 100, "width": 500, "height": 400,
                  "client_x": 101, "client_y": 120, "client_width": 498, "client_height": 380}
        geometry = mac_capture.capture_geometry(options, self.monitors, window)
        self.assertEqual(geometry["crop"], {"x": 202, "y": 240, "width": 996, "height": 760})
        self.assertIn("crop=996:760:202:240:exact=1", recorder._filters(options, geometry))

    def test_cross_monitor_region_rejected(self):
        options = recorder.normalize_options({"mode": "region", "region": {"x": -100, "y": 0,
                                                                                 "width": 200, "height": 200}})
        with self.assertRaisesRegex(ValueError, "同一显示器"):
            mac_capture.capture_geometry(options, self.monitors)

    def test_system_sound_requires_explicit_virtual_device(self):
        geometry = {"display_index": 0}
        for extra in ({}, {"system_index": 0}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                mac_capture.capture_plan(recorder.normalize_options({"audio": "system", **extra}), geometry, self.inventory)

    def test_overlay_both_audio_maps_inputs_without_windows_devices(self):
        options = recorder.normalize_options({"camera": "FaceTime HD Camera", "audio": "both",
                                              "system_index": 1, "microphone_index": 0})
        plan = mac_capture.capture_plan(options, {"display_index": 0}, self.inventory)
        self.assertEqual([plan["inputs"][i + 1] for i, arg in enumerate(plan["inputs"]) if arg == "-i"],
                         ["1:1", "0:none", "none:0"])
        self.assertEqual(plan["audio_labels"], ["0:a", "2:a"])
        self.assertNotIn("gdigrab", plan["inputs"])
        self.assertNotIn("dshow", plan["inputs"])
        self.assertIn("[2:a]", mac_capture.audio_filter(plan["audio_labels"]))

    def test_camera_only_microphone_shares_avfoundation_session(self):
        options = recorder.normalize_options({"mode": "camera", "camera": "FaceTime HD Camera", "audio": "microphone"})
        plan = mac_capture.capture_plan(options, {}, self.inventory)
        self.assertEqual(plan["inputs"][-1], "0:default")
        self.assertEqual(plan["audio_labels"], ["0:a"])


def wait_for(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError("Timed out waiting for recording state")


class OptionTests(unittest.TestCase):
    def setUp(self):
        # Command tests stay on the Windows branch even on a macOS CI runner;
        # macOS plans are tested above without opening real device inventory.
        platform = patch("recorder.sys.platform", "win32")
        platform.start()
        self.addCleanup(platform.stop)

    def test_invalid_parameters(self):
        invalid = [{"mode": "other"}, {"audio": "other"}, {"fps": True}, {"fps": 29},
                   {"duration": float("nan")}, {"duration": float("inf")}, {"duration": -1},
                   {"cursor": "yes"}, {"mode": "window", "hwnd": True},
                   {"mode": "camera"}, {"system_index": -1}, {"camera": "foo\nbar"},
                   {"mode": "region", "region": {"x": 1, "y": 2, "width": 0, "height": 4}},
                   {"unexpected": 1}]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                recorder.normalize_options(data)

    def test_options_and_snapshots_are_copies(self):
        options = {"mode": "region", "region": {"x": -100, "y": 2, "width": 100, "height": 80}}
        normalized = recorder.normalize_options(options)
        normalized["region"]["x"] = 999
        self.assertEqual(options["region"]["x"], -100)
        instance = recorder.Recorder()
        state = instance.snapshot()
        state["options"]["audio"] = "both"
        self.assertEqual(instance.snapshot()["options"], {})

    def test_hwnd_capture_and_negative_desktop_coordinates(self):
        geometry = {"x": -1920, "y": -200, "width": 1920, "height": 1080}
        with patch("capture_native.window_info"):
            args = recorder.capture_inputs(recorder.normalize_options({"mode": "window", "hwnd": 123}), geometry)
        self.assertIn("hwnd=123", args)
        self.assertNotIn("desktop", args)
        args = recorder.capture_inputs(recorder.normalize_options({}), geometry)
        self.assertEqual(args[args.index("-offset_x") + 1], "-1920")
        self.assertEqual(args[args.index("-offset_y") + 1], "-200")

    def test_region_cannot_escape_desktop(self):
        options = recorder.normalize_options({"mode": "region", "region": {
            "x": -1, "y": 0, "width": 100, "height": 100}})
        with patch("capture_native.desktop_bounds", return_value={"x": 0, "y": 0, "width": 640, "height": 480}):
            with self.assertRaisesRegex(ValueError, "超出"):
                recorder._geometry(options)

    def test_device_list_handles_missing_dependencies(self):
        with patch("recorder._ffmpeg", side_effect=RuntimeError("missing")), patch(
                "recorder.audio_inventory", side_effect=RuntimeError("missing audio")):
            result = recorder.device_inventory()
        self.assertEqual(result["cameras"], [])
        self.assertEqual(len(result["diagnostics"]), 2)

    def test_cleanup_preserves_unknown_files_and_rejects_wrong_root(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            recovery = destination / ".videocatch-recording-test"
            recovery.mkdir()
            owned = recovery / "finalize.log"
            owned.write_text("test")
            unknown = recovery / "user-notes.txt"
            unknown.write_text("keep")
            with self.assertRaisesRegex(RuntimeError, "未知文件"):
                recorder._cleanup_recovery(recovery, destination, "test")
            self.assertTrue(owned.exists())
            self.assertEqual(unknown.read_text(), "keep")
            with self.assertRaisesRegex(RuntimeError, "校验失败"):
                recorder._cleanup_recovery(recovery, destination, "another")


class RecordingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = recorder.ffmpeg_path()
        if not cls.ffmpeg:
            raise unittest.SkipTest("FFmpeg unavailable")

    def setUp(self):
        platform = patch("recorder.sys.platform", "win32")
        platform.start()
        self.addCleanup(platform.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.instance = recorder.Recorder()
        self.addCleanup(self.stop_worker)
        self.geometry = patch("recorder._geometry", return_value={"x": 0, "y": 0, "width": 160, "height": 120})
        self.geometry.start()
        self.addCleanup(self.geometry.stop)
        self.inputs = patch("recorder.capture_inputs", return_value=[
            "-re", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=15"])
        self.inputs.start()
        self.addCleanup(self.inputs.stop)

    def stop_worker(self):
        self.instance.stop()
        if self.instance._thread:
            self.instance._thread.join(20)

    def decode(self, path):
        result = subprocess.run([self.ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
                                capture_output=True, timeout=20, creationflags=recorder.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def first_rgb_frame(self, path):
        result = subprocess.run([self.ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0",
                                 "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                                capture_output=True, timeout=20, creationflags=recorder.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(len(result.stdout), 160 * 120 * 3)
        return lambda x, y: tuple(result.stdout[(y * 160 + x) * 3:(y * 160 + x) * 3 + 3])

    def test_camera_overlay_composes_real_dual_source_pixels(self):
        inputs = ["-re", "-f", "lavfi", "-i", "color=c=blue:size=160x120:rate=15",
                  "-re", "-f", "lavfi", "-i", "color=c=red:size=80x60:rate=15"]
        with patch("recorder.capture_inputs", return_value=inputs):
            self.instance.start({"camera": "Synthetic camera", "fps": 15, "duration": .3}, self.folder)
            wait_for(lambda: not self.instance.is_busy)
        state = self.instance.snapshot()
        self.assertEqual(state["status"], "已保存", state)
        self.decode(state["path"])
        pixel = self.first_rgb_frame(state["path"])
        # 40 x 30 overlay is inset 8 px from the bottom right of a 160 x 120 base.
        self.assertGreater(pixel(130, 95)[0], 220, pixel(130, 95))
        self.assertLess(pixel(130, 95)[2], 30, pixel(130, 95))
        for point in [(10, 10), (155, 115)]:
            self.assertGreater(pixel(*point)[2], 220, pixel(*point))
            self.assertLess(pixel(*point)[0], 30, pixel(*point))

    def test_camera_only_uses_camera_frame_and_preserves_aspect(self):
        inputs = ["-re", "-f", "lavfi", "-i", "color=c=lime:size=128x72:rate=15"]
        with patch("recorder.capture_inputs", return_value=inputs):
            self.instance.start({"mode": "camera", "camera": "Synthetic camera",
                                 "fps": 15, "duration": .3}, self.folder)
            wait_for(lambda: not self.instance.is_busy)
        state = self.instance.snapshot()
        self.assertEqual(state["status"], "已保存", state)
        self.decode(state["path"])
        pixel = self.first_rgb_frame(state["path"])
        self.assertGreater(pixel(80, 60)[1], 220, pixel(80, 60))
        self.assertLess(pixel(80, 60)[0], 30, pixel(80, 60))
        self.assertLess(max(pixel(80, 5)), 20, pixel(80, 5))

    def test_pause_resume_stop_produces_playable_mp4(self):
        started = time.monotonic()
        self.instance.start({"fps": 15}, self.folder)
        self.assertLess(time.monotonic() - started, .5)
        with self.assertRaises(ValueError):
            self.instance.start({}, self.folder)
        wait_for(lambda: self.instance.snapshot()["status"] == "录制中")
        time.sleep(.4)
        self.instance.pause()
        frozen = self.instance.snapshot()["elapsed"]
        time.sleep(.4)
        self.assertEqual(self.instance.snapshot()["elapsed"], frozen)
        self.instance.resume()
        wait_for(lambda: self.instance.snapshot()["status"] == "录制中")
        time.sleep(.4)
        started = time.monotonic()
        self.instance.stop()
        self.assertLess(time.monotonic() - started, .5)
        wait_for(lambda: not self.instance.is_busy)
        state = self.instance.snapshot()
        self.assertEqual(state["status"], "已保存", state)
        self.assertEqual(state["recovery_path"], "")
        self.assertEqual(list(self.folder.glob(".videocatch-recording-*")), [])
        self.decode(state["path"])

    def test_duration_stops_without_ui_polling(self):
        self.instance.start({"fps": 15, "duration": .5}, self.folder)
        wait_for(lambda: not self.instance.is_busy)
        state = self.instance.snapshot()
        self.assertEqual(state["status"], "已保存", state)
        self.assertGreaterEqual(state["elapsed"], .5)
        self.decode(state["path"])

    def test_immediate_pause_resume_still_closes_segment(self):
        with patch.object(self.instance, "_finalize", wraps=self.instance._finalize) as finalize:
            self.instance.start({"fps": 15}, self.folder)
            wait_for(lambda: self.instance.snapshot()["status"] == "录制中")
            self.instance.pause()
            self.instance.resume()
            wait_for(lambda: self.instance.snapshot()["status"] == "录制中")
            time.sleep(.3)
            self.instance.stop()
            wait_for(lambda: not self.instance.is_busy)
            self.assertEqual(self.instance.snapshot()["status"], "已保存", self.instance.snapshot())
            self.assertEqual(len(finalize.call_args.args[1]), 2)

    def test_odd_screenshot_keeps_exact_pixel_size(self):
        with patch("recorder.capture_inputs", return_value=[
                "-f", "lavfi", "-i", "testsrc=size=161x123:rate=1"]):
            result = recorder.screenshot({}, self.folder)
        self.assertEqual(result["status"], "已保存", result)
        png = Path(result["path"]).read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (161, 123))

    def test_ffmpeg_error_is_reported_and_recovery_retained(self):
        with patch("recorder.capture_inputs", return_value=["-f", "lavfi", "-i", "invalid_fixture"]):
            self.instance.start({}, self.folder)
            wait_for(lambda: not self.instance.is_busy)
        state = self.instance.snapshot()
        self.assertEqual(state["status"], "失败")
        self.assertTrue(state["error"])
        self.assertTrue(Path(state["recovery_path"]).is_dir())

    def test_screenshot_is_unique_and_decodable(self):
        first = recorder.screenshot({}, self.folder)
        second = recorder.screenshot({}, self.folder)
        self.assertEqual(first["status"], "已保存", first)
        self.assertEqual(second["status"], "已保存", second)
        self.assertNotEqual(first["path"], second["path"])
        self.decode(first["path"])

    def test_audio_mix_and_silent_loopback_finalize(self):
        raw = self.folder / "raw.mkv"
        result = subprocess.run([self.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                 "testsrc2=size=160x120:rate=15", "-t", "0.8", "-c:v", "libx264", str(raw)],
                                capture_output=True, timeout=20, creationflags=recorder.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        tracks = []
        for number in range(2):
            path = self.folder / f"audio{number}.wav"
            with wave.open(str(path), "wb") as output:
                output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                if number == 0:
                    output.writeframes(bytes(48000 * 2))
            tracks.append({"path": str(path), "started": 99.8 if number == 0 else None})
        final = self.folder / "mixed.mp4"
        recorder.Recorder._finalize(self.ffmpeg, [{"path": raw, "started": 100., "tracks": tracks}], self.folder, final)
        self.decode(final)
        probe = subprocess.run([self.ffmpeg, "-hide_banner", "-i", str(final)], capture_output=True,
                               timeout=10, creationflags=recorder.CREATE_NO_WINDOW)
        self.assertIn(b"Audio: aac", probe.stderr)
        original = final.read_bytes()
        # Existing destination is never overwritten.
        with self.assertRaises(RuntimeError):
            recorder.Recorder._finalize(self.ffmpeg, [{"path": raw, "started": 100., "tracks": tracks}], self.folder, final)
        self.assertEqual(final.read_bytes(), original)

    def test_macos_embedded_audio_survives_recording_and_finalization(self):
        source = self.folder / "synthetic-av.mkv"
        completed = subprocess.run([self.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                    "testsrc2=size=160x120:rate=15", "-f", "lavfi", "-i",
                                    "sine=frequency=440:sample_rate=48000", "-t", "5", "-c:v", "libx264",
                                    "-c:a", "aac", str(source)], capture_output=True, timeout=20,
                                   creationflags=recorder.CREATE_NO_WINDOW)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        geometry = {"x": 0, "y": 0, "width": 160, "height": 120, "display_index": 0}
        with patch("recorder.sys.platform", "darwin"), patch("recorder._geometry", return_value=geometry), patch(
                "recording_audio_macos.device_inventory", return_value=mac_capture.parse_devices(MAC_DEVICES)), patch(
                "recorder.capture_inputs", return_value=["-re", "-i", str(source)]), patch("recorder.AudioCapture") as windows_audio:
            self.instance.start({"audio": "microphone", "fps": 15, "duration": .5}, self.folder)
            wait_for(lambda: not self.instance.is_busy)
            windows_audio.assert_not_called()
        state = self.instance.snapshot()
        self.assertEqual(state["status"], "已保存", state)
        self.decode(state["path"])
        audio = subprocess.run([self.ffmpeg, "-v", "error", "-i", state["path"], "-map", "0:a:0",
                                "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                               capture_output=True, timeout=20, creationflags=recorder.CREATE_NO_WINDOW)
        self.assertEqual(audio.returncode, 0, audio.stderr.decode(errors="replace"))
        samples = struct.unpack("<" + "h" * (len(audio.stdout) // 2), audio.stdout)
        self.assertTrue(samples)
        self.assertGreater(max(abs(sample) for sample in samples), 1000)


if __name__ == "__main__":
    unittest.main()
