"""Real widget context-menu checks in isolated off-desktop Tk windows."""
import gc
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from context_menu import ContextMenu
from surface_ui import register_styles
from ui import PANEL, FIELD, MUTED, LINE


class ContextMenuTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('700x480+12000+12000')
        ttk.Style(self.root).theme_use('clam')
        register_styles(self.root)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.root.update()
        self.menu = ContextMenu(self.root)

    def tearDown(self):
        self.menu.close()
        try:
            if self.root is not None:
                self.root.destroy()
        except tk.TclError:
            pass
        self.menu = self.root = None
        gc.collect()
        self.assertEqual(self.errors, [])

    def show(self, items=None, title='已选 3 项'):
        popup = self.menu.show(12100, 12100, title, items or [
            {'label': '下载所选视频', 'icon': '↓', 'shortcut': 'Enter', 'command': Mock()},
            {'label': '复制视频链接', 'icon': '⧉', 'shortcut': 'Ctrl+C', 'command': Mock()},
            {'separator': True},
            {'label': '移除所选记录', 'icon': '×', 'danger': True, 'command': Mock()},
        ])
        self.root.update()
        return popup

    def test_themed_panel_with_scope_shortcuts_and_danger_without_native_menu(self):
        popup = self.show()
        self.assertTrue(popup.overrideredirect())
        self.assertFalse(bool(popup.attributes('-topmost')))
        self.assertEqual(popup.cget('bg'), LINE)
        labels = [widget.cget('text') for row in self.menu._rows for widget in row
                  if widget.winfo_class() == 'Label']
        self.assertIn('Ctrl+C', labels)
        self.assertEqual(self.menu.buttons[-1].cget('fg'), '#eda6b1')
        self.assertEqual(self.root.grab_current(), popup)
        self.assertEqual(popup.grab_status(), 'local')
        self.assertEqual(self.root.tk.call('after', 'info'), '')
        scale = max(1, popup.winfo_fpixels('1i') / 96)
        self.assertLessEqual(popup.winfo_width(), round(420 * scale))
        self.assertLess(popup.winfo_height(), round(350 * scale))

    def test_action_closes_and_releases_grab_before_callback(self):
        observations = []
        self.show([{'label': '操作', 'command': lambda: observations.append(
            (self.menu.window, self.root.grab_current(), self.menu.buttons[:]))}])
        self.menu.buttons[0].invoke()
        self.assertEqual(observations, [(None, None, [])])
        self.assertEqual(self.menu._commands, [])
        self.assertEqual(self.menu._bindings, [])

    def test_disabled_actions_are_never_invoked_and_keyboard_wraps_past_them(self):
        disabled, first, last = Mock(), Mock(), Mock()
        self.show([{'label': '不可用', 'enabled': False, 'command': disabled},
                   {'label': '复制', 'command': first}, {'separator': True},
                   {'label': '不可用 2', 'enabled': False, 'command': disabled},
                   {'label': '打开', 'command': last}])
        self.menu.buttons[0].invoke()
        disabled.assert_not_called()
        self.assertEqual(self.menu.buttons[0].cget('disabledforeground'), MUTED)
        self.assertEqual(self.menu._active, 1)
        self.menu._move(-1)
        self.assertEqual(self.menu._active, 3)
        self.menu._move(1)
        self.assertEqual(self.menu._active, 1)
        self.menu._enter()
        first.assert_called_once_with()
        last.assert_not_called()

    def test_mouse_hover_highlights_row_and_outside_click_dismisses(self):
        popup = self.show()
        self.menu.buttons[1].event_generate('<Enter>')
        self.root.update()
        self.assertEqual(self.menu._active, 1)
        self.assertEqual(self.menu.buttons[1].cget('bg'), FIELD)
        self.assertEqual(self.menu.buttons[0].cget('bg'), PANEL)
        popup.event_generate('<ButtonPress-1>', x=-10, y=-10)
        self.root.update()
        self.assertIsNone(self.menu.window)
        self.assertIsNone(self.root.grab_current())

    def test_escape_closes_and_close_is_reusable_without_binding_accumulation(self):
        bindings = {sequence: self.root.bind(sequence) for sequence in ('<Destroy>', '<Unmap>')}
        for _ in range(4):
            self.show()
            self.menu._escape()
            self.menu.close()
            self.assertIsNone(self.menu.window)
            for sequence, original in bindings.items():
                self.assertEqual(self.root.bind(sequence), original)
        self.assertEqual(self.root.tk.call('after', 'info'), '')

    def test_focus_out_dismisses_without_requesting_foreground_focus(self):
        popup = self.show()
        with patch.object(popup, 'focus_get', return_value=None), \
             patch.object(tk.Misc, 'focus_set') as focus_set, \
             patch.object(tk.Misc, 'focus_force', side_effect=AssertionError('Forced focus')):
            popup.event_generate('<FocusOut>')
            self.root.update()
            self.assertIsNone(self.menu.window)
            focus_set.assert_not_called()

    def test_close_restores_previous_focus_only_if_menu_owned_focus(self):
        entry = tk.Entry(self.root)
        entry.pack()
        self.root.update()
        with patch.object(self.root, 'focus_get', return_value=entry):
            popup = self.show()
        with patch.object(popup, 'focus_get', return_value=popup), \
             patch.object(entry, 'focus_set') as restore, \
             patch.object(tk.Misc, 'focus_force', side_effect=AssertionError('Forced focus')):
            self.menu.close()
            restore.assert_called_once_with()
        with patch.object(self.root, 'focus_get', return_value=entry):
            popup = self.show()
        with patch.object(popup, 'focus_get', return_value=entry), \
             patch.object(entry, 'focus_set') as restore:
            self.menu.close()
            restore.assert_not_called()

    def test_owner_hide_and_popup_destroy_clear_bindings_and_references(self):
        popup = self.show()
        self.root.withdraw()
        self.root.update()
        self.assertIsNone(self.menu.window)
        self.assertFalse(popup.winfo_exists())
        self.root.deiconify()
        popup = self.show()
        popup.destroy()
        self.root.update()
        self.assertIsNone(self.menu.window)
        self.assertEqual(self.menu._bindings, [])
        self.assertEqual(self.menu.buttons, [])
        self.assertIsNone(self.menu._canvas)

    def test_owner_destroy_cleans_callbacks_and_does_not_keep_owner_alive(self):
        self.show()
        reference = weakref.ref(self.root)
        self.root.destroy()
        self.assertIsNone(self.menu.window)
        self.assertEqual(self.menu._commands, [])
        self.assertEqual(self.menu._bindings, [])
        self.root = None
        gc.collect()
        self.assertIsNone(reference())
        self.assertIsNone(self.menu.show(0, 0, '已关闭', []))

    def test_previous_local_grab_restored_but_other_active_grab_preserved(self):
        dialog = tk.Toplevel(self.root)
        dialog.geometry('300x200+12000+12000')
        self.root.update()
        try:
            dialog.grab_set()
            self.show()
            self.menu.close()
            self.assertIs(self.root.grab_current(), dialog)
            popup = self.show()
            popup.destroy()
            self.root.update()
            self.assertIs(self.root.grab_current(), dialog)
            self.show()
            self.root.grab_set()
            self.menu.close()
            self.assertIs(self.root.grab_current(), self.root)
        finally:
            self.root.grab_release()
            dialog.destroy()

    def test_show_failure_cleans_popup_callbacks_and_owner_bindings(self):
        before = self.root.bind('<Destroy>')
        with patch.object(tk.Toplevel, 'grab_set', side_effect=tk.TclError('fixture grab failure')):
            with self.assertRaises(tk.TclError):
                self.show()
        self.assertIsNone(self.menu.window)
        self.assertEqual(self.menu.buttons, [])
        self.assertEqual(self.menu._bindings, [])
        self.assertEqual(self.root.bind('<Destroy>'), before)
        self.assertEqual(self.root.winfo_children(), [])

    def test_action_can_open_real_dialog_after_menu_close_without_menu_timer(self):
        import dialogs
        results = []
        original = dialogs._Dialog
        def create(*args, **kwargs):
            self.assertIsNone(self.menu.window)
            self.assertIsNone(self.root.grab_current())
            self.assertEqual(self.root.tk.call('after', 'info'), '')
            dialog = original(*args, **kwargs)
            # Test-only action: one idle callback operates the new real widget.
            self.root.after_idle(dialog.buttons['no'].invoke)
            return dialog
        self.show([{'label': '移除', 'danger': True, 'command': lambda: results.append(
            dialogs.messagebox.askyesno('移除记录', '是否继续？', parent=self.root))}])
        with patch.object(dialogs, '_Dialog', side_effect=create):
            self.menu.buttons[0].invoke()
        self.assertEqual(results, [False])
        self.assertIsNone(self.root.grab_current())
        self.assertEqual(self.root.tk.call('after', 'info'), '')

    def test_negative_monitor_placement_keeps_absolute_coordinates(self):
        self.root.geometry('700x480+-1800+12000')
        self.root.update()
        with patch('context_menu.owner_work_area', return_value=(-1920, 12000, 0, 12720)):
            popup = self.menu.show(-10, 12710, '已选 3 项', [{'label': '下载所选视频'}])
        self.root.update()
        self.assertLess(popup.winfo_rootx(), 0)
        self.assertGreaterEqual(popup.winfo_rootx(), -1920)
        self.assertLessEqual(popup.winfo_rootx() + popup.winfo_width(), 0)
        self.assertGreaterEqual(popup.winfo_rooty(), 12000)
        self.assertLessEqual(popup.winfo_rooty() + popup.winfo_height(), 12720)

    def test_small_high_dpi_display_keeps_action_buttons_inside_scroll_view(self):
        for scale in (1, 1.5, 2):
            with self.subTest(scale=scale):
                self.menu.close()
                self.root.tk.call('tk', 'scaling', scale * 96 / 72)
                with patch('context_menu.owner_work_area', return_value=(12000, 12000, 12640, 12480)), \
                     patch.object(tk.Toplevel, 'winfo_fpixels', return_value=96 * scale):
                    popup = self.show([{'label': '下载这几个选中视频 ' * 3,
                                        'shortcut': 'Ctrl+Shift+Enter', 'icon': '↓'} for _ in range(9)])
                self.assertLessEqual(popup.winfo_rootx() + popup.winfo_width(), 12640)
                self.assertLessEqual(popup.winfo_rooty() + popup.winfo_height(), 12480)
                for index in range(len(self.menu.buttons)):
                    self.menu._move(1)
                    self.root.update()
                    button = self.menu.buttons[self.menu._active]
                    self.assertGreaterEqual(button.winfo_rootx(), popup.winfo_rootx())
                    self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                         popup.winfo_rootx() + popup.winfo_width())
                    self.assertGreaterEqual(button.winfo_rooty(), self.menu._canvas.winfo_rooty() - 2)
                    self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                         self.menu._canvas.winfo_rooty() + self.menu._canvas.winfo_height() + 2)


if __name__ == '__main__':
    unittest.main()
