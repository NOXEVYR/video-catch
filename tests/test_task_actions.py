import gc
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import App


class TaskActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.open()

    def open(self):
        self.root = tk.Tk()
        self.root.geometry('1100x750')
        self.app = App(self.root, smoke=True, state_dir=self.folder/'state')
        self.app.show_page('tasks')
        self.root.update()

    def tearDown(self):
        self.app.jobs.clear()
        self.app.pending.clear()
        self.app.close()
        self.app = self.root = None
        gc.collect()
        self.temp.cleanup()

    def add(self, name, status='已保存'):
        path = self.folder / (name + '.png')
        image = tk.PhotoImage(master=self.root, width=2, height=2)
        image.write(str(path), format='png')
        ident = self.app.store.add({'url': f'https://fixture.test/{name}'}, manual=True)['id']
        self.app.store.update(ident, status=status, path=str(path), title=name)
        self.app.library.add(path, source='screenshot')
        self.app.library.save()
        self.app.persist_receipts()
        self.app.refresh()
        self.root.update()
        return ident, path

    def test_single_row_action_persists_and_preserves_library_and_file(self):
        ident, path = self.add('one')
        other, _ = self.add('two')
        tree = self.app.task_tree
        x,y,w,h = tree.bbox(ident, 'action')
        event = SimpleNamespace(x=x+w//2, y=y+h//2)
        self.app.task_action_press(event)
        self.app.task_action_release(event)
        self.assertNotIn(ident, self.app.store.items)
        self.assertIn(other, self.app.store.items)
        self.assertTrue(path.exists())
        self.assertIn(self.app.library.key(path), self.app.library.entries)
        self.app.close()
        self.open()
        self.assertNotIn(ident, self.app.store.items)
        self.assertIn(other, self.app.store.items)
        self.assertIn(self.app.library.key(path), self.app.library.entries)

    def test_failed_persistence_keeps_history_and_original_name(self):
        ident, _ = self.add('one')
        with patch.object(self.app.local_state, 'save_receipts', return_value=False):
            self.assertFalse(self.app.remove_records([ident]))
            self.assertIn(ident, self.app.task_tree.get_children())
            self.app.media.selection_set(ident)
            with patch('app.simpledialog.askstring', return_value='renamed'):
                self.app.rename_selected()
            self.assertEqual(self.app.store.items[ident]['title'], 'one')
            self.assertIn('未能保存', self.app.notice.get())

    def test_mixed_selection_deletes_finished_only_and_updates_buttons(self):
        done, _ = self.add('done')
        busy, _ = self.add('busy', '下载中')
        self.app.task_tree.selection_set(done, busy)
        self.app.update_task_actions()
        self.assertEqual(str(self.app.task_buttons['delete'].cget('state')), 'normal')
        self.assertTrue(self.app.remove_records([done, busy]))
        self.assertIn(busy, self.app.store.items)
        self.assertNotIn(done, self.app.store.items)
        self.app.task_tree.selection_set(busy)
        self.app.update_task_actions()
        self.assertEqual(str(self.app.task_buttons['delete'].cget('state')), 'disabled')
        self.assertEqual(str(self.app.task_buttons['cancel'].cget('state')), 'normal')

    def test_live_job_protects_record_even_after_status_transition(self):
        ident, _ = self.add('one')
        self.app.jobs[ident] = object()
        self.assertFalse(self.app.remove_records([ident]))
        self.assertIn(ident, self.app.store.items)

    def test_drag_over_action_does_not_delete(self):
        ident, _ = self.add('one')
        x,y,w,h = self.app.task_tree.bbox(ident, 'action')
        self.app.task_action_press(SimpleNamespace(x=x+10, y=y+h//2))
        self.app.task_action_release(SimpleNamespace(x=x+30, y=y+h//2))
        self.assertIn(ident, self.app.store.items)

    def test_missing_result_has_actionable_message(self):
        ident, path = self.add('missing')
        path.unlink()
        self.app.task_tree.selection_set(ident)
        self.app.open_task_result()
        self.assertIn('移动或删除', self.app.notice.get())

    def test_library_failure_does_not_escape_ui_callback(self):
        ident, _ = self.add('one')
        self.app.task_tree.selection_set(ident)
        with patch.object(self.app, 'open_library', side_effect=RuntimeError('素材目录保存失败')):
            self.app.open_task_result()
        self.assertIn('无法打开成果', self.app.notice.get())
        self.assertIn(ident, self.app.store.items)

    def test_page_switch_cleans_marquee_and_capture(self):
        app = self.app
        app.show_page('library')
        self.root.update()
        gallery = app.library_window
        x,y = gallery.canvas.winfo_rootx(), gallery.canvas.winfo_rooty()
        gallery.begin_marquee(SimpleNamespace(x_root=x+2,y_root=y+2,state=0))
        app.show_page('tasks')
        self.assertIsNone(gallery.marquee)
        self.assertIsNone(gallery._marquee_timer)
        self.assertIsNone(gallery.canvas.grab_current())


if __name__ == '__main__':
    unittest.main()
