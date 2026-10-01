"""Synthetic off-screen selection and atomic metadata actions; no native input."""
import gc
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import App


class NamingPatternTests(unittest.TestCase):
    def test_closed_tokens_and_replacement_are_literal(self):
        self.assertEqual(App.media_display_names('{name}_{n}', ['甲', '{n}']), ['甲_1', '{n}_2'])
        self.assertEqual(App.media_display_names('素材_{n}', ['甲', '乙']), ['素材_1', '素材_2'])
        self.assertEqual(App.media_display_names('{name.__class__}', ['甲'], batch=False), ['{name.__class__}'])

    def test_unsupported_fields_and_all_generated_lengths_are_rejected(self):
        for pattern in ('{name.__class__}', '{name[0]}', '{n:03}', '{n!r}', '{unknown}', '{}', '{{n}}', '{n', 'n}', '', 'a\n', 'a\x00'):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                App.media_display_names(pattern, ['甲', '乙'])
        with self.assertRaises(ValueError):
            App.media_display_names('{name}_{n}', ['甲', 'a' * 120])
        with self.assertRaises(ValueError):
            App.media_display_names('{name}_{n}', ['甲', 'a\n'])
        with self.assertRaises(ValueError):
            App.media_display_names('a' * 119 + '{n}', ['x'] * 10)
        self.assertEqual(len(App.media_display_names('a' * 119 + '{n}', ['x'])[0]), 120)


class WebListHeightTests(unittest.TestCase):
    def test_four_complete_rows_at_three_dpi_and_narrow_stack(self):
        for scale in (1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                root = tk.Tk()
                root.withdraw()
                root.tk.call('tk', 'scaling', scale * 96 / 72)
                app = None
                errors = []
                root.report_callback_exception = lambda *error: errors.append(error)
                try:
                    with patch.object(root, 'winfo_fpixels', return_value=scale * 96):
                        app = App(root, smoke=True)
                    root.minsize(600, 600)
                    root.geometry('1020x640+12000+12000')
                    root.deiconify()
                    app.show_page('browser')
                    app.store.sync('fixture-browser', '合成浏览器', [
                        dict(id=index, title=f'合成页面 {index}', url=f'https://fixture.test/page/{index}')
                        for index in range(6)])
                    for index in range(6):
                        item = app.store.add({'url': f'https://fixture.test/media/{index}'}, manual=True)
                        app.store.update(item['id'], title=f'合成素材 {index}', error='这是合成详情，用于检查换行后的列表高度。' * 15)
                    app.refresh()
                    app.media.selection_set(app.media.get_children()[0])
                    for width in (1020, 720):
                        root.geometry(f'{width}x640+12000+12000')
                        root.update()
                        for settings_open in (False, True):
                            if settings_open:
                                app.toggle_download_settings()
                            root.update()
                            for tree in (app.tabs, app.media):
                                tree.yview_moveto(0)
                                root.update()
                                fourth = tree.get_children()[3]
                                bounds = tree.bbox(fourth)
                                self.assertTrue(bounds, (scale, width, settings_open, tree))
                                self.assertLessEqual(bounds[1] + bounds[3], tree.winfo_height(),
                                                     (scale, width, settings_open, tree, tree.winfo_reqheight(),
                                                      app.source_split.winfo_height(), app.source_split.sashpos(0),
                                                      [(child.winfo_class(), child.winfo_reqheight(), child.winfo_height())
                                                       for child in tree.master.master.master.pack_slaves()],
                                                      app.workbench_canvas.cget('scrollregion')))
                            self.assertEqual(app.workbench_canvas.xview(), (0.0, 1.0))
                            self.assertLess(app.workbench_canvas.yview()[1] - app.workbench_canvas.yview()[0], 1)
                            for button in (*app.media_buttons.values(), app.pause_button):
                                self.assertLessEqual(button.winfo_x() + button.winfo_width(), button.master.winfo_width())
                        app.toggle_download_settings()
                    self.assertEqual(errors, [])
                finally:
                    if app is not None:
                        app.close()
                    else:
                        root.destroy()
                    app = root = tree = button = None
                    gc.collect()


class WebBatchActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, smoke=True, state_dir=self.folder / 'state')
        self.root.geometry('1180x850+12000+12000')
        self.root.deiconify()
        self.app.show_page('browser')
        self.root.update()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)

    def tearDown(self):
        self.app.jobs.clear()
        self.app.pending.clear()
        self.app.close()
        self.app = self.root = None
        gc.collect()
        self.temp.cleanup()

    def add(self, names=('甲', '乙', '丙', '丁'), status='已保存'):
        ids = []
        for index, name in enumerate(names):
            path = self.folder / f'{index}.png'
            image = tk.PhotoImage(master=self.root, width=2, height=2)
            image.write(str(path), format='png')
            ident = self.app.store.add({'url': f'https://fixture.test/{index}'}, manual=True)['id']
            self.app.store.update(ident, title=name, status=status, path=str(path))
            self.app.library.add(path, source='screenshot')
            ids.append(ident)
        self.app.library.save()
        self.app.persist_receipts()
        self.app.refresh()
        self.root.update()
        return ids

    def event(self, ident, state=0):
        self.app.media.see(ident)
        self.root.update()
        x, y, width, height = self.app.media.bbox(ident, 'title')
        return SimpleNamespace(x=x+20, y=y+height//2, x_root=12000+x+20,
                               y_root=12000+y+height//2, state=state)

    def click(self, ident, state=0):
        event = self.event(ident, state)
        # event_generate exercises bindtags, including the Treeview class.
        self.app.media.event_generate('<ButtonPress-1>', x=event.x, y=event.y, state=state)
        self.app.media.event_generate('<ButtonRelease-1>', x=event.x, y=event.y, state=state)
        self.root.update()

    def test_ctrl_shift_class_bindings_and_keyboard_select_clear(self):
        ids = self.add()
        self.click(ids[0])
        self.click(ids[2], 4)
        self.assertEqual(self.app.media.selection(), (ids[0], ids[2]))
        self.click(ids[3], 1)
        self.assertEqual(self.app.media.selection(), tuple(ids[2:]))
        self.click(ids[0], 5)
        self.assertEqual(self.app.media.selection(), tuple(ids))
        self.click(ids[1], 4)
        self.assertEqual(self.app.media.selection(), (ids[0], ids[2], ids[3]))
        self.app.media.event_generate('<Control-a>')
        self.root.update()
        self.assertEqual(self.app.media.selection(), tuple(ids))
        self.assertIn('已选 4 项', self.app.media_selection_text.get())
        self.app.media.event_generate('<Escape>')
        self.root.update()
        self.assertFalse(self.app.media.selection())
        self.assertEqual(self.callback_errors, [])

    def test_group_press_drag_handoff_and_plain_click_collapse(self):
        ids = self.add()
        self.app.media.selection_set(ids[:2])
        event = self.event(ids[0])
        self.assertEqual(self.app.begin_media_drag(event), 'break')
        self.assertEqual(self.app.media.selection(), tuple(ids[:2]))
        with patch.object(self.app, 'start_native_drag') as drag:
            self.app.drag_media(SimpleNamespace(x_root=event.x_root+20, y_root=event.y_root+10))
            self.app.release_media_drag(event)
        drag.assert_called_once_with([self.app.store.items[ident]['path'] for ident in ids[:2]])
        self.assertEqual(self.app.media.selection(), tuple(ids[:2]))
        self.click(ids[0])
        self.assertEqual(self.app.media.selection(), (ids[0],))
        self.click(ids[1], 4)
        self.assertIsNone(self.app._media_drag_start)

    def test_context_group_unselected_and_blank_all(self):
        ids = self.add()
        self.app.media.selection_set(ids[:2])
        with patch.object(self.app.media_menu, 'show') as shown:
            self.app.media_context_menu(self.event(ids[0]))
            self.assertEqual(self.app.media.selection(), tuple(ids[:2]))
            self.assertIn('已选 2 项', shown.call_args.args[2])
            self.app.media_context_menu(self.event(ids[2]))
            self.assertEqual(self.app.media.selection(), (ids[2],))
            self.app.clear_media_selection()
            self.app.media_context_menu(SimpleNamespace(y=self.app.media.winfo_height()-1, x_root=12000, y_root=12000))
            actions = shown.call_args.args[3]
            select_all = next(item for item in actions if item['label'] == '全选')
            self.assertTrue(select_all['enabled'])
            select_all['command']()
            self.assertEqual(self.app.media.selection(), tuple(ids))

    def test_real_shared_menu_rename_handoff_releases_grab(self):
        ids = self.add(('甲', '乙'))
        self.app.media.selection_set(ids)
        with patch('context_menu.owner_work_area', return_value=(12000, 12000, 13500, 13000)):
            self.app.media_context_menu(self.event(ids[0]))
            self.root.update()
        menu = self.app.media_menu
        self.assertGreater(menu.window.winfo_rootx(), 10000)
        clip = next(button for button in menu.buttons if button.cget('text').startswith('剪辑'))
        self.assertEqual(clip.cget('state'), 'disabled')
        rename = next(button for button in menu.buttons if '显示名称' in button.cget('text'))
        def dialog(*_args, **_kwargs):
            self.assertIsNone(menu.window)
            self.assertIsNone(self.root.grab_current())
            return '素材_{n}'
        with patch('app.simpledialog.askstring', side_effect=dialog):
            rename.invoke()
        self.assertEqual([self.app.store.items[ident]['title'] for ident in ids], ['素材_1', '素材_2'])
        self.assertEqual(self.app.media.selection(), tuple(ids))
        self.assertIsNone(menu.window)
        self.assertEqual(self.callback_errors, [])

    def test_page_switch_closes_web_task_and_gallery_menus(self):
        ident = self.add(('甲',))[0]
        with patch('context_menu.owner_work_area', return_value=(12000, 12000, 13500, 13000)):
            self.app.media_context_menu(self.event(ident))
            self.root.update()
            self.app.show_page('tasks')
            self.assertIsNone(self.app.media_menu.window)
            self.assertIsNone(self.root.grab_current())
            self.root.update()
            self.app.task_menu.show(12100, 12100, '合成任务', [dict(label='测试', command=lambda: None)])
            self.root.update()
            self.app.show_page('library')
            self.assertIsNone(self.app.task_menu.window)
            self.assertIsNone(self.root.grab_current())
            from context_menu import ContextMenu
            gallery = self.app.library_window
            old = getattr(gallery, 'context_menu', None)
            test_menu = ContextMenu(self.root)
            gallery.context_menu = test_menu
            try:
                test_menu.show(12100, 12100, '合成素材库', [dict(label='测试', command=lambda: None)])
                self.root.update()
                self.app.show_page('browser')
                self.assertIsNone(test_menu.window)
                self.assertIsNone(self.root.grab_current())
            finally:
                test_menu.close()
                gallery.context_menu = old

    def test_batch_rename_single_commit_preserves_files_and_library(self):
        ids = self.add(('甲', '乙', '丙'))
        self.app.media.selection_set(ids[:2])
        originals = {ident: dict(self.app.store.items[ident]) for ident in ids}
        library = dict(self.app.library.entries)
        save = self.app.local_state.save_receipts
        def commit(records):
            self.assertEqual([self.app.store.items[ident]['title'] for ident in ids], ['甲', '乙', '丙'])
            return save(records)
        with patch('app.simpledialog.askstring', return_value='{name}_{n}'), \
             patch.object(self.app.local_state, 'save_receipts', side_effect=commit) as saved:
            self.app.rename_selected()
        saved.assert_called_once()
        self.assertEqual([self.app.store.items[ident]['title'] for ident in ids], ['甲_1', '乙_2', '丙'])
        self.assertEqual([self.app.store.items[ident]['display_name'] for ident in ids[:2]], ['甲_1', '乙_2'])
        self.assertEqual([item['title'] for item in self.app.local_state.load_receipts()], ['甲_1', '乙_2', '2.png'])
        self.assertEqual(library, self.app.library.entries)
        for ident in ids:
            self.assertEqual(self.app.store.items[ident]['path'], originals[ident]['path'])
            self.assertTrue(Path(originals[ident]['path']).is_file())

    def test_cancel_invalid_name_and_save_failure_roll_back_entire_group(self):
        ids = self.add(('甲', 'b' * 120))
        self.app.media.selection_set(ids)
        before = {ident: dict(self.app.store.items[ident]) for ident in ids}
        for value in (None, '{name}_{n}', '{name.__class__}', 'bad\n'):
            with self.subTest(value=value), patch('app.simpledialog.askstring', return_value=value), \
                 patch.object(self.app.local_state, 'save_receipts') as saved:
                self.app.rename_selected()
                saved.assert_not_called()
                self.assertEqual(before, self.app.store.items)
        for failure in (False, OSError('fixture full')):
            with patch('app.simpledialog.askstring', return_value='素材_{n}'), \
                 patch.object(self.app.local_state, 'save_receipts', **({'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure})):
                self.app.rename_selected()
            self.assertEqual(before, self.app.store.items)
            self.assertIn('未能保存', self.app.notice.get())

    def test_single_rename_keeps_literal_braces_semantics(self):
        ident = self.add(('甲',))[0]
        self.app.media.selection_set(ident)
        with patch('app.simpledialog.askstring', return_value='  {name}_单项  '):
            self.app.rename_selected()
        self.assertEqual(self.app.store.items[ident]['title'], '{name}_单项')

    def test_delete_mixed_busy_and_live_job_preserves_assets(self):
        ids = self.add()
        self.app.store.update(ids[1], status='下载中')
        self.app.jobs[ids[2]] = object()
        paths = [Path(self.app.store.items[ident]['path']) for ident in ids]
        library = dict(self.app.library.entries)
        self.app.select_all_media()
        self.app.clear_selected()
        self.assertEqual(set(self.app.store.items), {ids[1], ids[2]})
        self.assertTrue(all(path.exists() for path in paths))
        self.assertEqual(library, self.app.library.entries)
        self.assertIn('2 条执行中的任务已保留', self.app.notice.get())
        self.app.jobs.clear()

    def test_delete_write_failure_keeps_every_record(self):
        ids = self.add()
        self.app.select_all_media()
        for failure in (False, OSError('fixture full')):
            with patch.object(self.app.local_state, 'save_receipts', **({'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure})):
                self.app.clear_selected()
            self.assertEqual(set(self.app.store.items), set(ids))
            self.assertEqual(set(self.app.media.get_children()), set(ids))

    def test_batch_download_cancel_and_single_clip_guard(self):
        ids = self.add(status='待保存')
        self.app.folder.set(str(self.folder / 'downloads'))
        self.app.select_all_media()
        self.app.enqueue()
        self.assertEqual(len(self.app.pending), 4)
        self.assertTrue(all(item['status'] == '排队中' for item in self.app.store.items.values()))
        self.app.clip_selected()
        self.assertIn('选择一个视频', self.app.notice.get())
        self.assertFalse(self.app.clip_after_download)
        self.assertFalse(self.app.clip_windows)
        self.app.cancel_selected()
        self.assertFalse(self.app.pending)
        self.assertTrue(all(item['status'] == '已取消' for item in self.app.store.items.values()))

    def test_action_row_wraps_at_small_width(self):
        self.add()
        self.root.minsize(600, 600)
        self.root.geometry('720x680+12000+12000')
        self.root.update()
        buttons = [*self.app.media_buttons.values(), self.app.pause_button]
        self.assertGreater(len({button.winfo_y() for button in buttons}), 1)
        for button in buttons:
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth()-4)
            self.assertLessEqual(button.winfo_x()+button.winfo_width(), button.master.winfo_width())
        self.assertEqual(self.callback_errors, [])


if __name__ == '__main__':
    unittest.main()
