import gc
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import App
from engine import ffmpeg_path
from media_library import Library
from library_ui import LibraryWindow, thumbnails
from file_actions import FileResult


class LibraryTests(unittest.TestCase):
    def test_journal_metadata_failure_protects_catalogue_readonly(self):
        with tempfile.TemporaryDirectory() as temp:
            original = Path.exists
            def exists(path):
                if path.name == 'library-recycle-journal.json':
                    raise PermissionError('fixture')
                return original(path)
            with patch('media_library.Path.exists', exists):
                library = Library(temp)
            self.assertTrue(library.readonly)
            self.assertFalse(library.save())

    def test_cleanup_error_after_commit_does_not_roll_back_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / 'sample.flv'
            video.write_bytes(b'fixture')
            library = Library(temp)
            library.add(video)
            with patch('media_library.Path.unlink', side_effect=PermissionError('fixture cleanup')):
                self.assertTrue(library.save())
            self.assertEqual(library.entries, Library(temp).entries)

    def test_invalid_entry_after_valid_entry_never_overwrites_catalogue(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / 'sample.mp4'
            video.write_bytes(b'fixture')
            library = Library(temp)
            library.add(video)
            data = {'schema': 2, 'entries': dict(library.entries)}
            data['entries']['invalid'] = {'path': 'relative'}
            text = json.dumps(data)
            library.path.write_text(text, encoding='utf-8')
            restored = Library(temp)
            self.assertTrue(restored.readonly)
            self.assertFalse(restored.save())
            self.assertEqual(restored.path.read_text(), text)

    def test_png_source_filters_and_schema1_migration_preserve_original(self):
        with tempfile.TemporaryDirectory() as temp:
            screenshot = Path(temp) / 'screenshot.png'
            screenshot.write_bytes(b'fixture')
            library = Library(temp)
            key = library.key(screenshot)
            legacy = {'schema': 1, 'entries': {key: {'path': str(screenshot), 'name': screenshot.name,
                      'hidden': False, 'added': 1}}}
            library.path.write_text(json.dumps(legacy), encoding='utf-8')
            library = Library(temp)
            self.assertEqual(library.rows(media_type='image', source='legacy')[0][0], key)
            self.assertTrue(library.save())
            backups = list(Path(temp).glob('library.schema1-backup-*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text()), legacy)
            self.assertEqual(screenshot.read_bytes(), b'fixture')

    def test_missing_pending_recycle_is_not_marked_recycled(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'sample.png'
            source.write_bytes(b'fixture')
            library = Library(temp)
            library.add(source)
            library.save()
            key = library.key(source)
            self.assertTrue(library.begin_recycle([key]))
            source.rename(Path(temp) / 'external-move.png')
            restored = Library(temp)
            self.assertFalse(restored.entries[key]['recycled'])
            self.assertFalse(restored.entries[key]['hidden'])
            self.assertTrue(restored.journal.exists())
            self.assertIn('未确认', restored.error)

    def test_confirmed_results_survive_index_write_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'sample.png'
            source.write_bytes(b'fixture')
            library = Library(temp)
            library.add(source)
            library.save()
            key = library.key(source)
            library.begin_recycle([key])
            source.rename(Path(temp) / 'fixture-recycle-target.png')
            library.record_recycle_result(FileResult(str(source), True))
            with patch('media_library.os.replace', side_effect=PermissionError('fixture')):
                self.assertFalse(library.recover_recycle())
            self.assertTrue(library.entries[key]['recycled'])
            self.assertTrue(library.journal.exists())
            restored = Library(temp)
            self.assertTrue(restored.entries[key]['recycled'])
            self.assertTrue(restored.entries[key]['hidden'])
            self.assertFalse(restored.journal.exists())

    def test_restart_remove_and_reimport_preserve_original(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / '收藏 视频.mp4'
            video.write_bytes(b'fixture')
            library = Library(temp)
            library.collect([{'path': str(video), 'status': '已保存'}])
            key = library.key(video)
            library.rename(key, '新的显示名')
            self.assertEqual(Library(temp).rows()[0][1]['name'], '新的显示名')
            library.remove([key])
            restored = Library(temp)
            restored.collect([{'path': str(video), 'status': '已保存'}])
            self.assertEqual(restored.rows(), [])
            self.assertEqual(video.read_bytes(), b'fixture')
            restored.add(video, restore=True)
            self.assertEqual(len(restored.rows()), 1)
            with self.assertRaises(ValueError):
                restored.rename(key, '\n')

    def test_corrupt_catalogue_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'library.json'
            path.write_text('broken', encoding='utf-8')
            library = Library(temp)
            self.assertFalse(library.save())
            self.assertEqual(path.read_text(), 'broken')

    def test_catalogue_metadata_access_failure_is_readonly(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('media_library.Path.exists', side_effect=PermissionError('fixture')):
                library = Library(temp)
            self.assertTrue(library.readonly)
            self.assertTrue(library.error)
            self.assertFalse(library.save())

    def test_inaccessible_historical_video_does_not_block_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Library(temp)
            with patch('media_library.Path.is_file', side_effect=PermissionError('fixture')):
                self.assertFalse(library.collect([{'status': '已保存', 'path': str(Path(temp) / 'old.mp4')}]))
            self.assertEqual(library.rows(), [])

    def test_failed_rename_or_remove_keeps_visible_committed_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / '样片.mp4'
            video.write_bytes(b'fixture')
            library = Library(temp)
            self.assertTrue(library.add(video))
            self.assertTrue(library.save())
            key = library.key(video)
            original_name = library.rows()[0][1]['name']
            with patch('media_library.os.replace', side_effect=PermissionError('fixture')):
                self.assertFalse(library.rename(key, '不能持久化的名称'))
                self.assertEqual(library.rows()[0][1]['name'], original_name)
                self.assertFalse(library.remove([key]))
                self.assertEqual(len(library.rows()), 1)
            self.assertEqual(Library(temp).rows()[0][1]['name'], original_name)

    def test_thumbnail_timeout_does_not_kill_worker_queue(self):
        requests, results = queue.Queue(), queue.Queue()
        stop, lock, decoders = threading.Event(), threading.Lock(), set()

        class FakeDecoder:
            def __init__(self, path):
                self.path = path

            def frame(self, seconds):
                if self.path == 'slow.mp4':
                    raise subprocess.TimeoutExpired('ffmpeg', 20)
                return b'next-frame'

            def close(self):
                pass

        requests.put((1, 'slow', 'slow.mp4'))
        requests.put((1, 'next', 'next.mp4'))
        worker = threading.Thread(target=thumbnails, args=(requests, results, stop, decoders, lock), daemon=True)
        errors = []
        with patch('library_ui.Decoder', FakeDecoder), patch('threading.excepthook', lambda args: errors.append(args.exc_value)):
            worker.start()
            try:
                self.assertEqual(results.get(timeout=1), (1, 'slow', None))
                self.assertEqual(results.get(timeout=1), (1, 'next', b'next-frame'))
                self.assertEqual(errors, [])
            finally:
                stop.set()
                worker.join(timeout=1)

    def test_thumbnail_decoder_creation_failure_does_not_kill_worker_queue(self):
        requests, results = queue.Queue(), queue.Queue()
        stop, lock, decoders = threading.Event(), threading.Lock(), set()

        class FakeDecoder:
            def __init__(self, path):
                if path == 'bad-link.mp4':
                    raise OSError('fixture path failure')

            def frame(self, seconds):
                return b'next-frame'

            def close(self):
                pass

        requests.put((1, 'bad', 'bad-link.mp4'))
        requests.put((1, 'next', 'next.mp4'))
        worker = threading.Thread(target=thumbnails, args=(requests, results, stop, decoders, lock), daemon=True)
        errors = []
        with patch('library_ui.Decoder', FakeDecoder), patch('threading.excepthook', lambda args: errors.append(args.exc_value)):
            worker.start()
            try:
                self.assertEqual(results.get(timeout=1), (1, 'bad', None))
                self.assertEqual(results.get(timeout=1), (1, 'next', b'next-frame'))
                self.assertEqual(errors, [])
            finally:
                stop.set()
                worker.join(timeout=1)

    def test_gallery_close_releases_tk_even_if_decoder_kill_fails(self):
        gallery = LibraryWindow.__new__(LibraryWindow)
        gallery.stop = threading.Event()
        gallery.lock = threading.Lock()
        gallery.decoders = {Mock(close=Mock(side_effect=OSError('fixture kill race')))}
        gallery.window = Mock()
        gallery.timer = 'timer'
        gallery.photos = {'frame': object()}
        gallery.app = Mock(library_window=gallery)
        gallery.close()
        self.assertTrue(gallery.stop.is_set())
        gallery.window.after_cancel.assert_called_once_with('timer')
        gallery.window.destroy.assert_called_once()
        self.assertEqual(gallery.photos, {})
        self.assertIsNone(gallery.app.library_window)

    def test_gallery_thumbnail_clip_drag_remove_and_close(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / '蓝色样片.mp4'
            subprocess.run([ffmpeg_path(), '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=320x180:r=12', '-t', '0.5', str(video)], check=True)
            root = tk.Tk()
            app = App(root, smoke=True)
            try:
                app.library.add(video)
                app.open_library()
                gallery = app.library_window
                key = app.library.key(video)
                deadline = time.monotonic() + 10
                while not gallery.photos and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.03)
                self.assertIn(key, gallery.photos)
                with patch.object(app, 'start_native_drag', return_value=True) as drag:
                    gallery.drag_start = (0, 0, key)
                    gallery.drag(type('Event', (), {'x_root': 20, 'y_root': 0})())
                    self.assertEqual(drag.call_args.args[0], [str(video)])
                gallery.remove([key])
                self.assertTrue(video.is_file())
                self.assertEqual(app.library.rows(), [])
                gallery.close()
                self.assertTrue(gallery.stop.is_set())
                self.assertIsNone(app.library_window)
                app.open_library()
                root.update()
            finally:
                app.close()
                app = root = None
                gc.collect()


class EmbeddedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.geometry('1000x700')
        self.parent = ttk.Frame(self.root)
        self.parent.pack(fill='both', expand=True)
        self.library = Library(self.directory)
        photo = tk.PhotoImage(master=self.root, width=80, height=45)
        photo.put('#F3B78F', to=(0, 0, 80, 45))
        screenshot = self.directory / '截图.png'
        photo.write(str(screenshot), format='png')
        png = screenshot.read_bytes()
        self.library.add(screenshot, source='screenshot')
        for index in range(15):
            video = self.directory / f'video-{index}.mp4'
            video.write_bytes(b'generated fixture')
            self.library.add(video, source='record')
        self.library.save()
        class FixtureDecoder:
            def __init__(self, path):
                pass
            def frame(self, seconds):
                return png
            def close(self):
                pass
        self.decoder = patch('library_ui.Decoder', FixtureDecoder)
        self.decoder.start()
        self.app = SimpleNamespace(root=self.root, library=self.library, library_window=None,
            folder=tk.StringVar(master=self.root, value=str(self.directory)), collect_library=Mock(),
            media_path_busy=Mock(return_value=False), media_recycled=Mock(), open_clip_editor=Mock(),
            start_native_drag=Mock(return_value=True), native_drag_active=False)
        self.gallery = LibraryWindow(self.app, parent=self.parent)
        self.app.library_window = self.gallery
        self.root.update()

    def tearDown(self):
        self.gallery.close()
        self.root.destroy()
        self.decoder.stop()
        self.temp.cleanup()

    def test_embedded_close_destroys_own_frame_only(self):
        self.assertIs(self.gallery.window, self.root)
        self.assertEqual(len(self.gallery.workers), 2)
        self.gallery.close()
        self.assertTrue(self.root.winfo_exists())
        self.assertTrue(self.parent.winfo_exists())
        self.assertFalse(self.gallery.frame.winfo_exists())
        self.assertTrue(all(not worker.is_alive() for worker in self.gallery.workers))

    def pointer(self, x, y, state=0):
        return SimpleNamespace(x_root=x, y_root=y, state=state, widget=self.gallery.canvas)

    def test_marquee_selects_intersecting_cards_without_starting_file_drag(self):
        gallery = self.gallery
        card = next(iter(gallery.cards.values()))
        x, y = card.winfo_rootx(), card.winfo_rooty()
        with patch('file_drag.drag_files') as drag:
            gallery.begin_marquee(self.pointer(x-3, y-3))
            gallery.update_marquee(self.pointer(x+card.winfo_width()+2, y+card.winfo_height()-3))
            self.assertEqual(gallery.selected_keys(), [next(iter(gallery.cards))])
            self.root.update_idletasks()
            if gallery._selection_overlay is not None:
                self.assertTrue(gallery._selection_overlay.visible)
                self.assertEqual(gallery._selection_edges, [])
                self.assertEqual(len(gallery._selection_overlay._canvas.find_all()), 1)
            else:
                self.assertTrue(all(edge.winfo_ismapped() for edge in gallery._selection_edges))
            gallery.end_marquee()
            if gallery._selection_overlay is not None:
                self.assertFalse(gallery._selection_overlay.visible)
            self.assertIsNone(gallery.canvas.grab_current())
            self.assertIsNone(gallery._marquee_timer)
            drag.assert_not_called()

    def test_native_mouse_bindings_select_and_release_marquee(self):
        gallery = self.gallery
        canvas = gallery.canvas
        key = next(iter(gallery.cards))
        card = gallery.cards[key]
        canvas.event_generate('<ButtonPress-1>', x=1, y=1)
        canvas.event_generate('<B1-Motion>', x=card.winfo_width()-5, y=80)
        self.root.update()
        self.assertEqual(gallery.selected_keys(), [key])
        canvas.event_generate('<ButtonRelease-1>', x=card.winfo_width()-5, y=80)
        self.root.update()
        self.assertIsNone(gallery.marquee)
        self.assertIsNone(canvas.grab_current())

    def test_missed_release_recovers_on_unpressed_motion(self):
        gallery = self.gallery
        x,y = gallery.canvas.winfo_rootx(), gallery.canvas.winfo_rooty()
        gallery.begin_marquee(self.pointer(x+1,y+1))
        gallery.update_marquee(self.pointer(x+150,y+90))
        gallery.recover_pointer_release(self.pointer(x+160,y+95,state=0))
        self.assertIsNone(gallery.marquee)
        self.assertIsNone(gallery._marquee_timer)
        self.assertIsNone(gallery.canvas.grab_current())
        self.root.update_idletasks()
        self.assertTrue(all(not edge.winfo_ismapped() for edge in gallery._selection_edges))
        if gallery._selection_overlay is not None:
            self.assertFalse(gallery._selection_overlay.visible)

    def test_window_hide_cleans_selection_gesture(self):
        gallery = self.gallery
        x,y = gallery.canvas.winfo_rootx(), gallery.canvas.winfo_rooty()
        gallery.begin_marquee(self.pointer(x+1,y+1))
        self.root.withdraw()
        self.root.update()
        self.assertIsNone(gallery.marquee)
        self.assertIsNone(gallery.canvas.grab_current())

    def test_ctrl_toggle_shift_range_and_drag_keep_selected_group(self):
        gallery = self.gallery
        keys = list(gallery.cards)
        gallery.select_card(keys[0])
        gallery.select_card(keys[2], 1)
        self.assertEqual(gallery.selected_keys(), keys[:3])
        gallery.select_card(keys[1], 4)
        self.assertEqual(gallery.selected_keys(), [keys[0], keys[2]])
        gallery.begin_drag(self.pointer(30, 30), keys[0])
        gallery.drag(self.pointer(50, 50))
        self.assertEqual(len(self.app.start_native_drag.call_args.args[0]), 2)

    def test_refresh_cancels_pending_source_and_removed_source_is_safe(self):
        gallery = self.gallery
        key = next(iter(gallery.cards))
        gallery.begin_drag(self.pointer(30,30),key)
        self.library.entries[key]['name'] = 'changed title'
        gallery.render()
        self.assertIsNone(gallery.drag_start)
        gallery.drag_start = (0,0,'removed')
        gallery.drag(self.pointer(50,50))
        self.app.start_native_drag.assert_not_called()

    def test_native_drag_defers_rebuild_until_result(self):
        gallery = self.gallery
        key = next(iter(gallery.cards))
        widget = gallery.cards[key]
        self.app.native_drag_active = True
        self.library.entries[key]['name'] = 'after drag'
        gallery.render()
        self.assertIs(gallery.cards[key],widget)
        self.app.native_drag_active = False
        gallery.render()
        self.assertIsNot(gallery.cards[key],widget)

    def test_escape_restores_selection_before_marquee_and_cleans_timer(self):
        gallery = self.gallery
        key = next(iter(gallery.cards))
        gallery.select_card(key)
        x, y = gallery.canvas.winfo_rootx(), gallery.canvas.winfo_rooty()
        gallery.begin_marquee(self.pointer(x+5, y+5))
        gallery.escape_selection(self.pointer(x+5, y+5))
        self.assertEqual(gallery.selected_keys(), [key])
        self.assertIsNone(gallery._marquee_timer)
        self.assertIsNone(gallery.canvas.grab_current())

    def test_filter_and_pagination_never_leave_hidden_batch_targets(self):
        gallery = self.gallery
        gallery.select_page()
        gallery.query.set('no matching fixture')
        gallery.render(reset=True)
        self.assertEqual(gallery.selected_keys(), [])
        gallery.query.set('')
        gallery.render(reset=True)
        gallery.select_page()
        gallery.move(1)
        self.assertEqual(gallery.selected_keys(), [])

    def test_marquee_auto_scrolls_and_closing_releases_capture(self):
        gallery = self.gallery
        c = gallery.canvas
        gallery.begin_marquee(self.pointer(c.winfo_rootx()+2, c.winfo_rooty()+30))
        gallery.update_marquee(self.pointer(c.winfo_rootx()+100, c.winfo_rooty()+c.winfo_height()-1))
        gallery.window.after_cancel(gallery._marquee_timer)
        gallery._marquee_tick()
        self.assertGreater(c.yview()[0], 0)
        gallery.close()
        self.assertIsNone(self.root.grab_current())
        self.assertIsNone(gallery._marquee_timer)

    def test_refresh_preserves_page_filter_selection_and_widgets_when_unchanged(self):
        gallery = self.gallery
        gallery.media_type.set('视频')
        gallery.source.set('录屏')
        gallery.sort.set('名称排序')
        gallery.page = 1
        gallery.render()
        key = next(iter(gallery.labels))
        gallery.selection[key].set(True)
        labels = dict(gallery.labels)
        generation = gallery.generation
        gallery.refresh()
        self.assertEqual(gallery.page, 1)
        self.assertEqual(gallery.source.get(), '录屏')
        self.assertEqual(gallery.media_type.get(), '视频')
        self.assertTrue(gallery.selection[key].get())
        self.assertEqual(gallery.labels, labels)
        self.assertEqual(gallery.generation, generation)

    def test_focus_path_clears_filters_and_selects_correct_page(self):
        gallery = self.gallery
        gallery.query.set('no matching result')
        gallery.media_type.set('截图 PNG')
        gallery.source.set('下载')
        gallery.render(reset=True)
        target = self.directory / 'video-0.mp4'
        self.assertTrue(gallery.focus_path(target))
        key = self.library.key(target)
        self.assertEqual(gallery.query.get(), '')
        self.assertEqual(gallery.media_type.get(), '全部类型')
        self.assertEqual(gallery.source.get(), '全部来源')
        self.assertIn(key, gallery.labels)
        self.assertTrue(gallery.selection[key].get())
        self.assertEqual(gallery.page, 1)
        from ui import ACCENT
        self.assertEqual(gallery.cards[key].cget('highlightbackground'), ACCENT)
        self.assertEqual(gallery.cards[key].cget('highlightthickness'), 2)

    def test_selection_traces_do_not_accumulate_after_rebuild(self):
        gallery = self.gallery
        key = next(iter(gallery.labels))
        variable = gallery.selection[key]
        self.assertEqual(len(variable.trace_info()), 1)
        for index in range(3):
            gallery.sort.set('名称排序' if index % 2 == 0 else '最近保存')
            gallery.render()
            self.assertLessEqual(len(variable.trace_info()), 1)
        gallery.close()
        self.assertEqual(variable.trace_info(), [])

    def test_200_percent_narrow_window_keeps_clip_delete_inside_cards(self):
        previous = self.root.tk.call('tk', 'scaling')
        try:
            self.root.tk.call('tk', 'scaling', 192 / 72)
            from tkinter import font
            font.nametofont('TkDefaultFont').configure(size=9)
            self.root.geometry('820x700')
            self.root.update()
            self.gallery._resize(SimpleNamespace(width=self.gallery.canvas.winfo_width()))
            self.root.update()
            self.assertEqual(self.gallery.columns, 1)
            for card in self.gallery.cards.values():
                actions = next(child for child in card.winfo_children() if isinstance(child, tk.Frame))
                for button in actions.winfo_children():
                    if isinstance(button, ttk.Button) and button.cget('text') in {'剪辑', '删除'}:
                        self.assertGreater(button.winfo_width(), 0)
                        self.assertGreaterEqual(button.winfo_x(), 0)
                        self.assertLessEqual(button.winfo_x() + button.winfo_width(), actions.winfo_width())
        finally:
            self.root.tk.call('tk', 'scaling', previous)

    def test_focus_path_readonly_or_failed_save_does_not_claim_success(self):
        self.library.readonly = True
        self.assertFalse(self.gallery.focus_path(self.directory / 'video-0.mp4'))
        self.library.readonly = False
        new = self.directory / 'new.png'
        new.write_bytes(b'fixture')
        with patch('media_library.os.replace', side_effect=PermissionError('fixture')):
            self.assertFalse(self.gallery.focus_path(new))
        self.assertNotIn(self.library.key(new), self.library.entries)

    def test_png_card_has_preview_delete_and_no_clip_action(self):
        self.gallery.media_type.set('截图 PNG')
        self.gallery.render(reset=True)
        card = next(child for child in self.gallery.grid.winfo_children() if isinstance(child, tk.Frame))
        actions = next(child for child in card.winfo_children() if isinstance(child, tk.Frame))
        buttons = [child.cget('text') for child in actions.winfo_children() if isinstance(child, ttk.Button)]
        self.assertIn('预览', buttons)
        self.assertIn('删除', buttons)
        self.assertNotIn('剪辑', buttons)
        delete = next(child for child in actions.winfo_children()
                      if isinstance(child, ttk.Button) and child.cget('text') == '删除')
        self.assertEqual(delete.cget('style'), 'Danger.TButton')

    def test_busy_delete_and_cancelled_confirmation_do_not_create_transaction(self):
        key = next(iter(self.gallery.labels))
        self.app.media_path_busy.return_value = True
        self.gallery.delete([key])
        self.assertFalse(self.library.journal.exists())
        self.app.media_path_busy.return_value = False
        with patch('library_ui.messagebox.askyesno', return_value=False):
            self.gallery.delete([key])
        self.assertFalse(self.library.journal.exists())
        self.assertTrue(Path(self.library.entries[key]['path']).exists())


if __name__ == '__main__':
    unittest.main()
