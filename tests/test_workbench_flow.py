import gc
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from app import App


class WorkbenchFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, smoke=True)

    def tearDown(self):
        self.app.close()
        self.app = self.root = None
        gc.collect()
        self.temp.cleanup()

    def test_complete_opens_library_once_and_defers_until_saving_finishes(self):
        path = Path(self.temp.name) / '完成.mp4'
        path.write_bytes(b'fixture')
        app = self.app
        with patch.object(app, 'open_library') as opened:
            app.queue_completed_media('one', str(path))
            app.screenshot_pending = True
            app.present_completed_media()
            opened.assert_not_called()
            app.screenshot_pending = False
            app.present_completed_media()
            opened.assert_called_once_with(str(path))
            app.queue_completed_media('one', str(path))
            app.present_completed_media()
            opened.assert_called_once()

    def test_completion_waits_for_new_capture_countdown(self):
        path = Path(self.temp.name) / '完成.mp4'
        path.write_bytes(b'fixture')
        from types import SimpleNamespace
        bar = SimpleNamespace(selector=None, _countdown='timer', _screenshot_timer=None, close=Mock())
        self.app.capture_bar = bar
        with patch.object(self.app, 'open_library') as opened:
            self.app.queue_completed_media('one', str(path))
            self.app.present_completed_media()
            opened.assert_not_called()
            bar._countdown = None
            self.app.present_completed_media()
            opened.assert_called_once_with(str(path))
            bar.close.assert_called_once()

    def test_transient_library_failure_preserves_completion_for_retry(self):
        path = Path(self.temp.name) / '完成.mp4'
        path.write_bytes(b'fixture')
        self.app.queue_completed_media('one', str(path))
        with patch.object(self.app, 'open_library', side_effect=[RuntimeError('temporary'), None]) as opened:
            self.app.present_completed_media()
            self.assertEqual(self.app._completion_pending, [str(path)])
            self.app.present_completed_media()
            self.assertEqual(opened.call_count, 1)
            self.app._completion_retry_at = 0
            self.app.present_completed_media()
            self.assertEqual(opened.call_count, 2)
            self.assertFalse(self.app._completion_pending)

    def test_saved_screenshot_event_selects_real_library_card(self):
        path = Path(self.temp.name) / '新截图.png'
        photo = tk.PhotoImage(master=self.root, width=2, height=2)
        photo.put('#ffc18e', to=(0, 0, 2, 2))
        photo.write(str(path), format='png')
        app = self.app
        app.screenshot_pending = True
        app._capture_item('saved-screen', 'screenshot', '截图', '截图中')
        app.capture_events.put(('screenshot', 'saved-screen', {'status': '已保存', 'path': str(path)}))
        app._refresh_capture()
        app.present_completed_media()
        key = app.library.key(path)
        self.assertEqual(app.current_page, 'library')
        self.assertEqual(app.library.entries[key]['media_type'], 'image')
        self.assertTrue(app.library_window.selection[key].get())
        self.assertFalse(app._completion_pending)

    def test_library_file_operation_blocks_new_editor_and_exit(self):
        app = self.app
        path = Path(self.temp.name) / 'busy.mp4'
        path.write_bytes(b'fixture')
        app.library_window.busy_paths.add(app.library.key(path))
        app.library_window.acting = True
        try:
            app.open_clip_editor(path)
            self.assertFalse(app.clip_windows)
            app.close()
            self.assertFalse(app.closing)
            self.assertTrue(self.root.winfo_exists())
        finally:
            app.library_window.acting = False
            app.library_window.busy_paths.clear()

    def test_exit_after_save_never_reopens_window(self):
        path = Path(self.temp.name) / '完成.mp4'
        path.write_bytes(b'fixture')
        self.app.exit_after_capture = True
        with patch.object(self.app, 'open_library') as opened:
            self.app.queue_completed_media('one', str(path))
            self.app.present_completed_media()
            opened.assert_not_called()
        self.app.exit_after_capture = False

    def test_busy_editor_protects_source_and_recycle_updates_receipts(self):
        path = Path(self.temp.name) / '素材.mp4'
        path.write_bytes(b'fixture')
        editor = Mock(source=str(path), closed=False)
        self.app.clip_windows = [editor]
        self.assertTrue(self.app.media_path_busy(path))
        self.app.clip_windows.clear()
        self.assertFalse(self.app.media_path_busy(path))
        item = self.app.store.add({'url':'https://example.test/v.mp4'}, manual=True)
        self.app.store.update(item['id'], status='已保存', path=str(path))
        self.app.media_recycled([str(path)])
        self.assertEqual(self.app.store.items[item['id']]['status'], '已取消')
        self.assertTrue(path.exists())  # Callback never performs filesystem deletion.

    def test_default_library_navigation_retains_discovery(self):
        self.assertEqual(self.app.current_page, 'library')
        self.app.focus_link_entry()
        self.assertEqual(self.app.current_page, 'browser')
        self.app.show_page('tasks')
        self.assertEqual(self.app.current_page, 'tasks')
        self.app.open_library(activate=False)
        self.assertEqual(self.app.current_page, 'library')


if __name__ == '__main__':
    unittest.main()
