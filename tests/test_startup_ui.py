"""Isolated startup lifecycle tests; never launch the installed application."""
from pathlib import Path
import sys
import subprocess
import threading
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from startup_ui import Splash, StartupSequence, StartupStage, icon_frame_at, ICON_INTRO, ICON_LOOP, ICON_FPS
from single_instance import SingleInstance


class FakeRoot:
    def __init__(self):
        self.calls = []
        self.pending = {}
        self.serial = 0

    def bind(self, *args, **kwargs):
        pass

    def after(self, delay, callback):
        self.serial += 1
        token = str(self.serial)
        self.pending[token] = callback
        self.calls.append(delay)
        return token

    def after_idle(self, callback):
        return self.after(0, callback)

    def after_cancel(self, token):
        self.pending.pop(token, None)

    def step(self):
        token = next(iter(self.pending))
        self.pending.pop(token)()

    def drain(self):
        for _ in range(100):
            if not self.pending:
                return
            self.step()
        raise AssertionError("Unexpected callback loop")


def fake_splash():
    return Mock(finished=False)


class SequenceTests(unittest.TestCase):
    def test_stages_follow_actual_work_without_dwell_time(self):
        root, splash = FakeRoot(), fake_splash()
        order = []
        sequence = StartupSequence(root, splash, [
            StartupStage("读取设置", lambda: order.append("settings")),
            StartupStage("建立工作台", lambda: order.append("ui")),
        ], on_complete=lambda: order.append("complete"))
        sequence.start()
        self.assertEqual(order, [])
        root.drain()
        self.assertEqual(order, ["settings", "ui", "complete"])
        self.assertTrue(all(delay == 0 for delay in root.calls))
        splash.finish.assert_called_once_with()

    def test_retry_resumes_failed_checkpoint_and_does_not_replay_previous_work(self):
        root, splash = FakeRoot(), fake_splash()
        first = Mock(return_value="ready")
        second = Mock(side_effect=[ValueError("private path must not be shown"), "recovered"])
        on_error = Mock()
        sequence = StartupSequence(root, splash, [StartupStage("设置", first),
            StartupStage("工作台", second)], on_error=on_error)
        sequence.start()
        root.drain()
        self.assertFalse(sequence.running)
        splash.finish.assert_not_called()
        self.assertNotIn("private", splash.fail.call_args.args[0])
        on_error.assert_called_once()
        sequence.retry()
        root.drain()
        first.assert_called_once()
        self.assertEqual(second.call_count, 2)
        self.assertEqual(sequence.results, ["ready", "recovered"])
        splash.finish.assert_called_once()

    def test_background_work_runs_off_tk_thread_and_finish_runs_on_tk_thread(self):
        root, splash = FakeRoot(), fake_splash()
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        threads = []
        main_thread = threading.get_ident()

        def action():
            threads.append(threading.get_ident())
            started.set()
            release.wait(2)
            done.set()
            return "result"

        complete = Mock(side_effect=lambda: threads.append(threading.get_ident()))
        sequence = StartupSequence(root, splash, [StartupStage("加载", action, background=True)], complete)
        sequence.start()
        root.step()
        root.step()
        self.assertTrue(started.wait(2))
        splash.finish.assert_not_called()
        self.assertIn(25, root.calls)
        release.set()
        self.assertTrue(done.wait(2))
        sequence._worker.join(2)
        root.drain()
        self.assertNotEqual(threads[0], main_thread)
        self.assertEqual(threads[1], main_thread)
        self.assertEqual(sequence.results, ["result"])

    def test_cancel_discards_worker_result_and_callbacks(self):
        root, splash = FakeRoot(), fake_splash()
        release, done = threading.Event(), threading.Event()

        def action():
            release.wait(2)
            done.set()

        sequence = StartupSequence(root, splash, [StartupStage("加载", action, True)])
        sequence.start()
        root.step()
        root.step()
        sequence.cancel()
        release.set()
        self.assertTrue(done.wait(2))
        sequence._worker.join(2)
        root.drain()
        splash.finish.assert_not_called()
        self.assertEqual(sequence.results, [])
        self.assertFalse(root.pending)

    def test_duplicate_start_does_not_execute_twice(self):
        root, splash = FakeRoot(), fake_splash()
        action = Mock()
        sequence = StartupSequence(root, splash, [StartupStage("加载", action)])
        sequence.start()
        sequence.start()
        root.drain()
        action.assert_called_once()

    def test_completion_failure_remains_retryable(self):
        root, splash = FakeRoot(), fake_splash()
        complete = Mock(side_effect=[RuntimeError("fixture"), None])
        sequence = StartupSequence(root, splash, [], complete)
        sequence.start()
        root.drain()
        splash.finish.assert_not_called()
        sequence.retry()
        root.drain()
        splash.finish.assert_called_once()


class RealTkSplashTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.update_idletasks()
        self.root.destroy()

    def test_failure_retry_reuses_window_and_finish_cancels_animation(self):
        retry = Mock()
        splash = Splash(self.root, on_retry=retry)
        self.root.update()
        self.assertEqual(self.root.state(), "withdrawn")
        original_window = splash.window
        self.assertIs(Splash(self.root), splash)
        self.assertIsNotNone(splash._timer)
        splash.fail("测试失败")
        self.assertIsNone(splash._timer)
        self.assertEqual(splash.retry_button.cget("state"), "normal")
        splash._retry()
        retry.assert_called_once()
        self.assertIs(splash.window, original_window)
        self.assertIsNotNone(splash._timer)
        splash.finish()
        self.root.update()
        self.assertEqual(self.root.state(), "normal")
        self.assertIsNone(splash._timer)
        self.assertFalse(splash.window.winfo_exists())
        splash.finish()
        self.assertIs(Splash(self.root), splash)

    def test_fast_start_finishes_in_first_event_loop_without_minimum_duration(self):
        splash = Splash(self.root)
        sequence = StartupSequence(self.root, splash, [StartupStage("准备", lambda: None)])
        sequence.start()
        self.root.update()
        self.assertTrue(splash.finished)
        self.assertEqual(self.root.state(), "normal")
        self.assertIsNone(splash._timer)

    def test_destroyed_splash_cancels_animation(self):
        splash = Splash(self.root)
        splash.window.destroy()
        self.root.update()
        self.assertTrue(splash.finished)
        self.assertIsNone(splash._timer)

    def test_icon_pixels_animate_without_accumulating_tk_images(self):
        splash = Splash(self.root)
        self.assertTrue(splash.icon_animation_available)
        images = set(self.root.tk.call("image", "names"))
        splash._draw_icon(0)
        first = self.root.tk.call(splash.image, "data", "-format", "png")
        for frame in range(1, ICON_INTRO + ICON_LOOP):
            splash._draw_icon(frame)
        self.assertNotEqual(self.root.tk.call(splash.image, "data", "-format", "png"), first)
        self.assertEqual(set(self.root.tk.call("image", "names")), images)
        splash.finish()
        self.assertIsNone(splash.image)
        self.assertIsNone(splash.icon_sheet)

    def test_failed_loading_rests_on_complete_icon_and_retry_restarts_intro(self):
        with patch("startup_ui.time.monotonic", return_value=100):
            splash = Splash(self.root, on_retry=Mock())
            splash._draw_icon(0)
            splash.fail()
            self.assertEqual(splash._icon_frame, ICON_INTRO)
            splash._retry()
            self.assertEqual(splash._icon_frame, 0)
            self.assertIsNotNone(splash._timer)

    def test_corrupt_motion_asset_falls_back_without_blocking_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder) / "assets"
            assets.mkdir()
            (assets / "startup-motion.png").write_bytes(b"broken fixture")
            # A static fallback remains available if only the atlas is damaged.
            original = Path(__file__).resolve().parents[1] / "assets/brand64.png"
            (assets / "brand64.png").write_bytes(original.read_bytes())
            with patch("runtime_paths.distribution_root", return_value=Path(folder)):
                splash = Splash(self.root)
            self.assertFalse(splash.icon_animation_available)
            self.assertEqual(splash.image.width(), 64)
            splash.finish()
            self.assertEqual(self.root.state(), "normal")


class IconTimingTests(unittest.TestCase):
    def test_loop_skips_intro_and_wraps_without_an_empty_frame(self):
        self.assertEqual(icon_frame_at(-1), 0)
        self.assertEqual(icon_frame_at(0), 0)
        self.assertEqual(icon_frame_at(ICON_INTRO / ICON_FPS), ICON_INTRO)
        for index in range(ICON_LOOP * 5):
            elapsed = (ICON_INTRO + index + .01) / ICON_FPS
            self.assertEqual(icon_frame_at(elapsed), ICON_INTRO + index % ICON_LOOP)

    def test_late_callback_uses_elapsed_time_instead_of_replaying_backlog(self):
        self.assertEqual(icon_frame_at(120), ICON_INTRO + (120 * ICON_FPS - ICON_INTRO) % ICON_LOOP)


@unittest.skipUnless(sys.platform == "win32", "Windows kernel objects")
class SingleInstanceTests(unittest.TestCase):
    def test_secondary_process_only_signals_primary(self):
        app_id = "VideoCatch.fixture." + uuid.uuid4().hex
        primary = SingleInstance(app_id)
        try:
            self.assertTrue(primary.acquire())
            code = ("from single_instance import SingleInstance; "
                    "import sys; item = SingleInstance(sys.argv[1]); "
                    "assert not item.acquire(); assert item.wake_existing(); item.close()")
            result = subprocess.run([sys.executable, "-c", code, app_id],
                                    cwd=str(Path(__file__).resolve().parents[1]),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(primary.poll())
            self.assertFalse(primary.poll())
        finally:
            primary.close()

    def test_isolated_primary_secondary_activation_and_release(self):
        app_id = "VideoCatch.fixture." + uuid.uuid4().hex
        primary, secondary, replacement = [SingleInstance(app_id) for _ in range(3)]
        try:
            self.assertTrue(primary.acquire())
            self.assertTrue(primary.primary)
            self.assertFalse(secondary.acquire())
            self.assertFalse(secondary.primary)
            self.assertFalse(primary.poll())
            self.assertTrue(secondary.wake_existing())
            self.assertTrue(primary.poll())
            self.assertFalse(primary.poll())
            primary.close()
            self.assertFalse(secondary.wake_existing(timeout_ms=0))
            self.assertTrue(replacement.acquire())
        finally:
            primary.close()
            secondary.close()
            replacement.close()

    def test_application_names_cannot_escape_namespace(self):
        with self.assertRaises(ValueError):
            SingleInstance("Global\\other")


if __name__ == "__main__":
    unittest.main()
