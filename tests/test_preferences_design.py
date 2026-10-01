"""Settings lifecycle and reachability using only an isolated Tk fixture."""
import gc
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preferences_ui import show_preferences
from surface_ui import register_styles
from ui import BG, FG, FIELD, PANEL, ACCENT


class PreferencesFixture:
    def __init__(self, root):
        self.root = root
        self.brand_image = tk.PhotoImage(master=root, width=1, height=1)
        self.preferences_window = None
        self.folder = tk.StringVar(root, 'fixture-media')
        self.remember_folder = tk.BooleanVar(root, False)
        self.ai_on_start = tk.BooleanVar(root, True)
        self.ai_enabled = tk.BooleanVar(root, True)
        self.diagnostics = tk.BooleanVar(root, False)
        self.notice = tk.StringVar(root, '设置就绪。')
        self.storage_settings = {'ai_on_start': True, 'trust_until': 0}
        self.local_state = SimpleNamespace(warnings=[])
        self.save_succeeds = True
        self.save_preferences = Mock(side_effect=self.save)
        self.grant_trust = Mock()
        self.revoke_trust = Mock()
        self.choose_folder = Mock()
        self.open_capture_settings = Mock()
        self.open_ai_collaboration = Mock()
        self.show_main = Mock()
        self.show_page = Mock()

    @property
    def bridge(self):
        raise AssertionError('Settings must not read the pairing token')

    def save(self):
        if not self.save_succeeds:
            self.notice.set('设置未能保存；原设置保持不变。')
            return False
        self.storage_settings.update(ai_on_start=self.ai_on_start.get(),
                                     diagnostics=self.diagnostics.get())
        if self.ai_on_start.get():
            self.storage_settings['trust_until'] = 0
        self.notice.set('设置已保存。')
        return True


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class PreferencesDesignTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry('860x650+12000+12000')
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 9), background=BG, foreground=FG)
        style.configure('TNotebook.Tab', padding=(16, 10))
        style.configure('TButton', padding=(14, 9), background=FIELD, foreground=FG)
        style.configure('Accent.TButton', background=ACCENT)
        style.configure('TEntry', fieldbackground=FIELD)
        style.configure('TNotebook', background=BG)
        style.map('TNotebook.Tab', background=[('selected', PANEL)])
        register_styles(self.root)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.app = PreferencesFixture(self.root)

    def tearDown(self):
        if self.app.preferences_window is not None:
            self.app.preferences_window.close()
        self.root.destroy()
        self.app = self.root = None
        gc.collect()
        self.assertEqual(self.errors, [])

    def open(self, section=None):
        window = show_preferences(self.app, section=section)
        window.geometry('760x650+12000+12000')
        self.root.update()
        return window

    def button(self, text):
        return next(widget for widget in descendants(self.app.preferences_window)
                    if widget.winfo_class() == 'TButton' and widget.cget('text') == text)

    def notebook(self):
        return next(widget for widget in descendants(self.app.preferences_window)
                    if isinstance(widget, ttk.Notebook))

    def visible_text(self):
        texts = []
        for widget in descendants(self.app.preferences_window):
            if widget.winfo_class() == 'TLabel':
                variable = widget.cget('textvariable')
                texts.append(str(widget.getvar(variable)) if variable else str(widget.cget('text')))
            elif isinstance(widget, tk.Text):
                texts.append(widget.get('1.0', 'end-1c'))
        return '\n'.join(texts)

    def test_external_ai_toggle_refreshes_and_close_removes_only_owned_traces(self):
        observer = Mock()
        outside = self.app.ai_enabled.trace_add('write', observer)
        before_ai = self.app.ai_enabled.trace_info()
        before_notice = self.app.notice.trace_info()
        for method in ('close', 'destroy'):
            window = self.open('AI 协作')
            self.app.ai_enabled.set(False)
            self.assertIn('当前 AI 协作：已关闭', self.visible_text())
            self.app.ai_enabled.set(True)
            self.assertIn('当前 AI 协作：已开启', self.visible_text())
            getattr(window, method)()
            self.assertEqual(self.app.ai_enabled.trace_info(), before_ai)
            self.assertEqual(self.app.notice.trace_info(), before_notice)
            self.app.ai_enabled.set(False)
            self.app.notice.set('关闭后的通知不会访问已销毁窗口。')
            window = None
            gc.collect()
        self.assertGreaterEqual(observer.call_count, 6)
        self.app.ai_enabled.trace_remove('write', outside)

    def test_long_feedback_preserves_full_message_and_footer_reachability(self):
        self.root.tk.call('tk', 'scaling', 96 / 72)
        window = self.open()
        message = '设置未能保存；' + '详细错误说明。' * 1500 + '\n原设置保持不变。'
        self.app.notice.set(message)
        self.root.update()
        feedback = next(widget for widget in descendants(window) if isinstance(widget, tk.Text))
        self.assertEqual(feedback.get('1.0', 'end-1c'), message)
        self.assertEqual(str(feedback.cget('state')), 'disabled')
        self.assertEqual(int(feedback.cget('height')), 2)
        self.assertLess(feedback.yview()[1], 1)
        feedback.yview_moveto(1)
        self.assertAlmostEqual(feedback.yview()[1], 1)
        for text in ('保存设置', '关闭'):
            button = self.button(text)
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                 window.winfo_rooty() + window.winfo_height())
        self.assertGreater(self.notebook().winfo_height(), 60)

    def test_default_section_and_reuse_keep_current_tab(self):
        window = self.open()
        tabs = self.notebook()
        self.assertEqual(tabs.tab(tabs.select(), 'text'), '采集与保存')
        self.assertIs(show_preferences(self.app, section='AI 协作'), window)
        self.assertEqual(tabs.tab(tabs.select(), 'text'), 'AI 协作')
        self.assertIs(show_preferences(self.app), window)
        self.assertEqual(tabs.tab(tabs.select(), 'text'), 'AI 协作')
        self.assertIs(show_preferences(self.app, section='不存在'), window)
        self.assertEqual(tabs.tab(tabs.select(), 'text'), 'AI 协作')
        self.assertIsNone(window.grab_current())
        self.assertFalse(bool(window.attributes('-topmost')))

    def test_actions_use_existing_callbacks_and_record_navigation(self):
        self.open('AI 协作')
        self.button('打开当前 AI 协作').invoke()
        self.app.open_ai_collaboration.assert_called_once_with()
        self.button('打开采集参数设置').invoke()
        self.app.open_capture_settings.assert_called_once_with()
        self.button('更改').invoke()
        self.app.choose_folder.assert_called_once_with()
        self.button('查看任务与清理记录').invoke()
        self.app.show_main.assert_called_once_with()
        self.app.show_page.assert_called_once_with('tasks')

    def test_saved_and_failed_settings_report_real_callback_status(self):
        self.open('AI 协作')
        self.app.ai_on_start.set(False)
        self.button('保存设置').invoke()
        self.assertFalse(self.app.storage_settings['ai_on_start'])
        self.assertIn('自动开启已关闭', self.visible_text())
        self.assertIn('设置已保存', self.visible_text())
        self.app.save_succeeds = False
        self.app.ai_on_start.set(True)
        self.button('保存设置').invoke()
        self.assertFalse(self.app.storage_settings['ai_on_start'])
        self.assertIn('设置未能保存', self.visible_text())
        self.assertEqual(self.app.save_preferences.call_count, 2)

    def test_grant_decline_and_revoke_refresh_without_extra_confirmation(self):
        window = self.open('AI 协作')
        self.button('授权 24 小时').invoke()
        self.app.grant_trust.assert_called_once_with()
        self.assertEqual(self.app.storage_settings['trust_until'], 0)
        self.app.storage_settings.update(ai_on_start=False, trust_until=2000000000)
        window.refresh_status()
        self.assertIn('重启恢复授权至', self.visible_text())

        def revoke():
            self.app.storage_settings.update(ai_on_start=False, trust_until=0)
            self.app.ai_enabled.set(False)
            self.app.ai_on_start.set(False)
            self.app.notice.set('已撤销重启恢复权限并关闭当前 AI 协作。')
        self.app.revoke_trust.side_effect = revoke
        self.button('撤销并关闭协作').invoke()
        self.app.revoke_trust.assert_called_once_with()
        self.assertIn('当前 AI 协作：已关闭', self.visible_text())
        self.assertNotIn('重启恢复授权至', self.visible_text())

    def test_default_folder_still_saves_and_disables_remember(self):
        self.open()
        self.app.remember_folder.set(True)
        with patch('pathlib.Path.home', return_value=Path('fixture-home')):
            self.button('恢复默认保存目录').invoke()
        self.assertFalse(self.app.remember_folder.get())
        folder = 'Movies' if sys.platform == 'darwin' else 'Videos'
        self.assertEqual(Path(self.app.folder.get()), Path('fixture-home') / folder / 'VideoCatch')
        self.app.save_preferences.assert_called_once_with()

    def test_minimum_window_at_three_scales_keeps_footer_and_focus_reachable(self):
        for scale in (1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                self.root.tk.call('tk', 'scaling', scale * 96 / 72)
                with patch.object(tk.Toplevel, 'winfo_screenwidth', return_value=1280), \
                     patch.object(tk.Toplevel, 'winfo_screenheight', return_value=720), \
                     patch.object(tk.Toplevel, 'winfo_fpixels', return_value=scale * 96):
                    window = self.open('AI 协作')
                width, height = window.minsize()
                window.geometry(f'{width}x{height}+12000+12000')
                self.root.update()
                tabs = self.notebook()
                self.assertGreater(tabs.winfo_height(), 60)
                for title in ('采集与保存', 'AI 协作', '诊断与恢复'):
                    window.select_section(title)
                    self.root.update()
                    for text in ('保存设置', '关闭'):
                        button = self.button(text)
                        self.assertTrue(button.winfo_ismapped())
                        self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth() - 4)
                        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                             window.winfo_rooty() + window.winfo_height())
                    page = self.root.nametowidget(tabs.select())
                    canvas = next(widget for widget in descendants(page) if isinstance(widget, tk.Canvas))
                    last_button = [widget for widget in descendants(page)
                                   if widget.winfo_class() == 'TButton'][-1]
                    canvas.yview_moveto(0)
                    last_button.event_generate('<FocusIn>')
                    self.root.update()
                    self.assertGreaterEqual(last_button.winfo_rooty(), canvas.winfo_rooty())
                    self.assertLessEqual(last_button.winfo_rooty() + last_button.winfo_height(),
                                         canvas.winfo_rooty() + canvas.winfo_height())
                    canvas.yview_moveto(1)
                    self.root.update()
                    self.assertAlmostEqual(canvas.yview()[1], 1.0)
                window.close()
                window = tabs = page = canvas = last_button = button = None
                gc.collect()

    def test_escape_destroy_and_reopen_release_refs_and_tk_callbacks(self):
        initial_after = self.root.tk.call('after', 'info')
        for method in ('escape', 'destroy', 'close'):
            window = self.open()
            ref = weakref.ref(window)
            close = window.close
            if method == 'escape':
                # Dispatch the widget binding without changing physical focus.
                script = window.bind('<Escape>')
                command = script.split('[', 1)[1].split()[0]
                self.root.tk.call(command, *(['0'] * 19))
            elif method == 'destroy':
                window.destroy()
            else:
                close()
            close()
            window.refresh_status()
            self.assertIsNone(self.app.preferences_window)
            self.assertEqual(self.root.tk.call('after', 'info'), initial_after)
            window = close = None
            gc.collect()
            self.assertIsNone(ref())


if __name__ == '__main__':
    unittest.main()
