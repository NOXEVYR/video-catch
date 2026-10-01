"""Clip lifecycle regression tests with no App construction or real media devices."""
import os
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from ai_api import Api
from app import App
from clip_drafts import ClipDrafts
from clip_ui import ClipEditor
from clipper import clip_worker, source_stamp


class FakeTimeline:
    times = [0.0, 1.0, 2.0, 3.0]
    end = 4.0

    def range(self, first, last):
        return self.times[first], self.times[last + 1] if last + 1 < len(self.times) else self.end


def editor_stub(source, drafts, timeline=None):
    editor = ClipEditor.__new__(ClipEditor)
    editor.source = str(source)
    editor.source_stamp = source_stamp(source)
    editor.timeline = timeline
    editor.first, editor.last, editor.cursor = 1, 2, 1
    editor.closed = editor.exported = editor.playing = False
    editor.timer, editor.play_timer = "poll-timer", "play-timer"
    editor.decoder = Mock()
    editor.window = Mock()
    editor.controls_body = Mock()
    editor.status = Mock()
    editor.app = SimpleNamespace(clip_drafts=drafts, notice=Mock(), closing=False,
                                 clip_windows=[editor], ai=SimpleNamespace(enqueue_clip=Mock()),
                                 persist_receipts=Mock())
    return editor


class ClipLifecycleTests(unittest.TestCase):
    def test_closing_while_index_loads_keeps_prior_draft_and_cancels_both_timers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source fixture")
            drafts = ClipDrafts(directory)
            previous = (source_stamp(source), 1, 2, 1)
            self.assertTrue(drafts.save(source, previous))
            editor = editor_stub(source, drafts, timeline=None)
            self.assertTrue(editor.close(prompt=False))
            self.assertEqual(ClipDrafts(directory).get(source), previous)
            self.assertTrue(editor.closed)
            self.assertEqual(editor.app.clip_windows, [])
            editor.window.after_cancel.assert_has_calls([call("poll-timer"), call("play-timer")])
            editor.decoder.close.assert_called_once()
            editor.window.destroy.assert_called_once()

    def test_failed_keep_and_discard_writes_leave_editor_and_selection_open(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"fixture")
            drafts = Mock(enabled=True)
            drafts.save.return_value = False
            drafts.get.return_value = ((1, 2, 3), 0, 2, 1)
            editor = editor_stub(source, drafts, timeline=FakeTimeline())
            with patch("clip_ui.messagebox.showerror") as error:
                self.assertFalse(editor.close(prompt=False, decision="keep"))
                self.assertFalse(editor.close(prompt=False, decision="discard"))
            self.assertEqual(error.call_count, 2)
            self.assertFalse(editor.closed)
            self.assertEqual((editor.first, editor.last), (1, 2))
            editor.window.destroy.assert_not_called()
            editor.decoder.close.assert_not_called()

    def test_app_cancel_on_second_editor_does_not_close_first(self):
        app = App.__new__(App)
        app.recorder = SimpleNamespace(is_busy=False)
        app.screenshot_pending = False
        app.exit_after_capture = app.closing = False
        app.jobs, app.pending = {}, []
        app.ai_enabled = SimpleNamespace(get=lambda: False)
        first = Mock(close_decision=Mock(return_value="discard"))
        second = Mock(close_decision=Mock(return_value=None))
        app.clip_windows = [first, second]
        app.close()
        first.close.assert_not_called()
        second.close.assert_not_called()
        self.assertEqual(app.clip_windows, [first, second])
        self.assertFalse(app.closing)

    def test_app_batch_save_failure_preserves_all_editors_and_drafts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'first.mp4'
            source.write_bytes(b'first')
            other = Path(directory) / 'second.mp4'
            other.write_bytes(b'second')
            drafts = ClipDrafts(directory)
            first = editor_stub(source, drafts, FakeTimeline())
            second = editor_stub(other, drafts, FakeTimeline())
            first.close_decision = Mock(return_value='discard')
            second.close_decision = Mock(return_value='keep')
            drafts.save(source, (source_stamp(source), 1, 2, 1))
            before = drafts.path.read_bytes()
            app = App.__new__(App)
            app.root = Mock()
            app.recorder = SimpleNamespace(is_busy=False)
            app.screenshot_pending = False
            app.exit_after_capture = app.closing = False
            app.jobs, app.pending = {}, []
            app.ai_enabled = SimpleNamespace(get=lambda: False)
            app.clip_drafts, app.clip_windows = drafts, [first, second]
            with patch('clip_drafts.os.replace', side_effect=PermissionError), patch('app.messagebox.showerror'):
                app.close()
            self.assertFalse(first.closed)
            self.assertFalse(second.closed)
            self.assertEqual(drafts.path.read_bytes(), before)
            self.assertFalse(app.closing)

    def test_replaced_source_invalidates_pending_timeline_and_blocks_export(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"original")
            editor = editor_stub(source, Mock(), timeline=None)
            editor.results = queue.Queue()
            editor.results.put(("timeline", FakeTimeline()))
            editor.export_button = Mock()
            replacement = Path(directory) / "replacement"
            replacement.write_bytes(b"replacement media")
            os.replace(replacement, source)
            editor.poll()
            self.assertIsNone(editor.timeline)
            editor.export_button.configure.assert_not_called()
            self.assertIn("源视频已变化", editor.status.set.call_args.args[0])
            editor.timeline = FakeTimeline()
            with patch("clip_ui.messagebox.showerror") as error:
                editor.export()
            error.assert_called_once()
            editor.app.ai.enqueue_clip.assert_not_called()

    def test_api_queue_records_fingerprint_and_worker_rejects_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"original")
            app = SimpleNamespace(folder=SimpleNamespace(get=lambda: directory),
                                  store=SimpleNamespace(lock=threading.Lock(), items={}), pending=[])
            result = Api(app).enqueue_clip({"source": str(source), "start": 0.0, "end": 1.0})
            item = app.store.items[result["id"]]
            self.assertEqual(tuple(item["source_stamp"]), source_stamp(source))
            replacement = Path(directory) / "replacement"
            replacement.write_bytes(b"replaced")
            os.replace(replacement, source)
            events = queue.Queue()
            with patch("clipper.ffmpeg_path", return_value="fake-ffmpeg"), patch("clipper.subprocess.run") as run:
                clip_worker(item, directory, events)
            run.assert_not_called()
            self.assertEqual(events.get_nowait()[1]["status"], "失败")
            self.assertFalse((Path(directory) / f"clip-{item['id']}.mp4").exists())

    def test_worker_rejects_source_change_during_encode_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"original")
            item = {"id": "fixture", "source_path": str(source), "source_stamp": source_stamp(source),
                    "start": 0.0, "end": 1.0}
            events = queue.Queue()

            def fake_run(command, **_kwargs):
                if "-progress" not in command:
                    return SimpleNamespace(stderr=b"Duration: 00:00:10.00, start: 0.0, bitrate: 0\nVideo: fake")
                Path(command[-1]).write_bytes(b"encoded-but-not-published")
                source.write_bytes(b"source changed while encoding")
                return SimpleNamespace(returncode=0, stdout=b"frame=1\n")

            with patch("clipper.ffmpeg_path", return_value="fake-ffmpeg"), \
                 patch("clipper.subprocess.run", side_effect=fake_run):
                clip_worker(item, directory, events)
            messages = [entry[1] for entry in list(events.queue)]
            self.assertEqual(messages[-1]["status"], "失败")
            self.assertFalse((Path(directory) / "clip-fixture.mp4").exists())


if __name__ == "__main__":
    unittest.main()
