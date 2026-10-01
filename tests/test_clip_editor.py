import gc
from pathlib import Path
import queue
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from app import App
from clip_preview import Decoder, Timeline
from clipper import clip_worker
from engine import ffmpeg_path


class ClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.video = cls.folder / "颜色 帧 fixture.mkv"
        subprocess.run([ffmpeg_path(), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10',
                        '-t', '1', '-vf', "select=not(eq(n\\,2)+eq(n\\,4))", '-fps_mode', 'vfr', '-c:v', 'ffv1', str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_vfr_preview_matches_decoded_frame_and_exports_single_frame(self):
        decoder = Decoder(self.video)
        self.addCleanup(decoder.close)
        timeline = decoder.timeline()
        self.assertEqual([round(t, 1) for t in timeline.times], [0, .1, .3, .5, .6, .7, .8, .9])
        for index in (0, 1, 2, 7):
            reference = subprocess.check_output([ffmpeg_path(), '-v', 'error', '-i', str(self.video), '-vf',
                f'select=eq(n\\,{index}),scale=640:360:force_original_aspect_ratio=decrease', '-frames:v', '1',
                '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'])
            self.assertEqual(decoder.frame(timeline.times[index]), reference)
        start, end = timeline.range(2, 2)
        events = queue.Queue()
        clip_worker({'id': 'single-vfr-frame', 'source_path': str(self.video), 'start': start, 'end': end}, str(self.folder), events)
        results = []
        while not events.empty(): results.append(events.get()[1])
        self.assertEqual(results[-1]['status'], '已保存', results)
        output = Decoder(results[-1]['path'])
        try: self.assertEqual(len(output.timeline().times), 1)
        finally: output.close()

    def test_invalid_timeline_and_cancelled_decoder(self):
        with self.assertRaises(ValueError): Timeline([0, 0], 1)
        decoder = Decoder(self.video); decoder.close()
        with self.assertRaises(RuntimeError): decoder.timeline()

    def test_editor_selection_keys_export_and_close(self):
        root = tk.Tk(); root.withdraw()
        app = App(root, smoke=True)
        try:
            app.folder.set(str(self.folder))
            app.open_clip_editor(self.video)
            editor = app.clip_windows[0]
            deadline = time.monotonic() + 15
            while (editor.timeline is None or editor.photo is None) and time.monotonic() < deadline:
                root.update(); time.sleep(.02)
            self.assertIsNotNone(editor.photo)
            editor.window.geometry('780x620'); root.update()
            self.assertTrue(editor.export_button.winfo_ismapped())
            editor.canvas.focus_force(); root.update()
            editor.canvas.event_generate('<Right>'); root.update()
            self.assertEqual(editor.cursor, 1)
            editor.mark_start()
            editor.target.set('last'); editor.step(-1)
            self.assertEqual(editor.last, 6)
            editor.target.set('first'); editor.step(1)
            self.assertEqual(editor.first, 2)
            self.assertEqual(editor.cursor, 2)
            expected = editor.timeline.range(2, 6)
            editor.export()
            self.assertTrue(editor.closed)
            self.assertEqual(len(app.pending), 1)
            task, _ = app.pending[0]
            self.assertEqual((task['start'], task['end']), expected)
            self.assertFalse(app.clip_windows)
        finally:
            app.pending.clear(); app.close()
            app = root = None; gc.collect()

    def test_close_and_reopen_retains_selection_for_same_file(self):
        root = tk.Tk(); root.withdraw()
        app = App(root, smoke=True)
        try:
            app.open_clip_editor(self.video)
            editor = app.clip_windows[0]
            deadline = time.monotonic() + 15
            while editor.timeline is None and time.monotonic() < deadline:
                root.update(); time.sleep(.02)
            self.assertIsNotNone(editor.timeline)
            editor.first, editor.last = 2, 6
            editor.show(3)
            editor.close(prompt=False)
            app.open_clip_editor(self.video)
            reopened = app.clip_windows[0]
            deadline = time.monotonic() + 15
            while reopened.timeline is None and time.monotonic() < deadline:
                root.update(); time.sleep(.02)
            self.assertEqual((reopened.first, reopened.last, reopened.cursor), (2, 6, 3))
            reopened.close(prompt=False)
        finally:
            app.close(); app = root = None; gc.collect()

    def test_saved_and_pending_source_flow(self):
        app = App.__new__(App)
        app.media = Mock(selection=Mock(return_value=('item',)))
        app.store = Mock(items={'item': {'id':'item', 'status':'已保存', 'path':str(self.video)}})
        app.notice = Mock(); app.open_clip_editor = Mock(); app.clip_after_download = set()
        app.clip_selected()
        app.open_clip_editor.assert_called_once_with(str(self.video))
        app.open_clip_editor.reset_mock()
        app.store.items['item']['status'] = '下载中'
        app.clip_selected()
        self.assertEqual(app.clip_after_download, {'item'})
        app.poll_clip_sources(); app.open_clip_editor.assert_not_called()
        app.store.items['item']['status'] = '已保存'
        app.poll_clip_sources(); app.open_clip_editor.assert_called_once()
        self.assertFalse(app.clip_after_download)
        app.clip_after_download.add('item'); app.store.items['item']['status'] = '失败'
        app.poll_clip_sources(); self.assertFalse(app.clip_after_download)

    def test_library_rename_and_remove_preserve_original_file(self):
        root = tk.Tk(); root.withdraw()
        app = App(root, smoke=True)
        try:
            ident = app.store.add({'url':'https://example.test/video.mp4'}, manual=True)['id']
            app.store.update(ident, status='已保存', path=str(self.video))
            app.refresh(); app.media.selection_set(ident)
            with patch('app.simpledialog.askstring', return_value='我的剪辑素材'):
                app.rename_selected()
            self.assertEqual(app.store.items[ident]['display_name'], '我的剪辑素材')
            app.clear_selected()
            self.assertNotIn(ident, app.store.items)
            self.assertTrue(self.video.exists())
        finally:
            app.close(); app = root = None; gc.collect()

if __name__ == '__main__': unittest.main()
