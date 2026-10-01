"""Real Tk modal tests using isolated, off-screen windows and Tcl callbacks."""
import gc
from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dialogs
from surface_ui import register_styles


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("600x400+12000+12000")
        register_styles(self.root)
        self.root.update()
        self.active = None
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.patches = [
            patch.object(dialogs._native_messagebox, "askyesno", side_effect=AssertionError("Unexpected native dialog")),
            patch.object(dialogs._native_messagebox, "askyesnocancel", side_effect=AssertionError("Unexpected native dialog")),
            patch.object(dialogs._native_messagebox, "showerror", side_effect=AssertionError("Unexpected native dialog")),
            patch.object(dialogs._native_messagebox, "showinfo", side_effect=AssertionError("Unexpected native dialog")),
            patch.object(dialogs._native_simpledialog, "askstring", side_effect=AssertionError("Unexpected native dialog")),
        ]
        for replacement in self.patches:
            replacement.start()

    def tearDown(self):
        for replacement in reversed(self.patches):
            replacement.stop()
        if dialogs._alive(self.root):
            for token in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.after_cancel(token)
            self.root.destroy()
        self.active = self.root = None
        self.patches.clear()
        gc.collect()

    def call_dialog(self, method, action, *args, **kwargs):
        original = dialogs._Dialog
        bindings_before = self.root.bind("<Destroy>")
        def create(*arguments, **options):
            self.active = original(*arguments, **options)
            return self.active
        def operate():
            # wait_visibility pumps timers before child layout may be mapped.
            # Observe a settled visible dialog, not a machine-speed delay.
            if self.active is None or not self.active.window.winfo_viewable():
                self.root.after(5, operate)
                return
            self.active.window.update_idletasks()
            try:
                self.assertIsNotNone(self.active)
                self.assertGreater(self.active.window.winfo_rootx(), 10000)
                self.assertGreater(self.active.window.winfo_rooty(), 10000)
                action(self.active)
            except Exception:
                self.errors.append(sys.exc_info())
                if self.active:
                    self.active.cancel()
        def timeout():
            self.errors.append((AssertionError, AssertionError("Modal wait did not complete"), None))
            if self.active:
                self.active.cancel()
        action_token = self.root.after(80, operate)
        timeout_token = self.root.after(2000, timeout)
        try:
            with patch.object(dialogs, "_Dialog", side_effect=create):
                result = method(*args, parent=self.root, **kwargs)
        finally:
            if dialogs._alive(self.root):
                self.root.after_cancel(action_token)
                self.root.after_cancel(timeout_token)
        if self.errors:
            kind, error, traceback = self.errors[0]
            raise error.with_traceback(traceback)
        self.assertFalse(dialogs._alive(self.active.window))
        if dialogs._alive(self.root):
            self.assertEqual(self.root.bind("<Destroy>"), bindings_before)
            self.assertEqual(self.root.tk.call("after", "info"), "")
        return result

    def test_yesno_returns_boolean_and_defaults_to_cancel(self):
        def cancel_by_enter(dialog):
            self.assertIs(dialog.default_button, dialog.buttons["no"])
            with patch.object(dialog.window, "focus_get", return_value=None):
                dialog._enter()
        self.assertIs(self.call_dialog(dialogs.messagebox.askyesno, cancel_by_enter,
                                       "删除素材", "是否删除？", default="yes"), False)
        self.assertIs(self.call_dialog(dialogs.messagebox.askyesno,
                                       lambda dialog: dialog.buttons["yes"].invoke(),
                                       "授权", "完整授权说明"), True)

    def test_enter_invokes_the_explicitly_focused_button(self):
        def enter_yes(dialog):
            with patch.object(dialog.window, "focus_get", return_value=dialog.buttons["yes"]):
                dialog._enter()
        self.assertIs(self.call_dialog(dialogs.messagebox.askyesno, enter_yes, "确认", "继续？"), True)

    def test_three_way_results_do_not_confuse_no_and_cancel(self):
        for key, expected in (("yes", True), ("no", False), ("cancel", None)):
            with self.subTest(key=key):
                self.assertIs(self.call_dialog(dialogs.messagebox.askyesnocancel,
                                               lambda dialog: dialog.buttons[key].invoke(),
                                               "保留剪辑选区", "是否保留？"), expected)

    def test_close_and_escape_cancel_without_authorizing(self):
        def close(dialog):
            dialog.window.tk.call(dialog.window.protocol("WM_DELETE_WINDOW"))
        for method, expected in ((dialogs.messagebox.askyesno, False),
                                 (dialogs.messagebox.askyesnocancel, None)):
            self.assertIs(self.call_dialog(method, close, "确认", "继续？"), expected)
        def escape(dialog):
            self.assertTrue(dialog.window.bind("<Escape>"))
            dialog.cancel()
        self.assertIsNone(self.call_dialog(dialogs.messagebox.askyesnocancel, escape, "确认", "继续？"))

    def test_rename_preserves_initial_value_and_accepts_empty_value(self):
        def rename(dialog):
            self.assertEqual(dialog.entry.get(), "原始显示名称")
            dialog.entry.delete(0, "end")
            dialog.entry.insert(0, "新显示名称")
            dialog.buttons["ok"].invoke()
        self.assertEqual(self.call_dialog(dialogs.simpledialog.askstring, rename,
                                          "修改素材名称", "显示名称：", initialvalue="原始显示名称"),
                         "新显示名称")
        def empty(dialog):
            dialog.entry.delete(0, "end")
            dialog.buttons["ok"].invoke()
        self.assertEqual(self.call_dialog(dialogs.simpledialog.askstring, empty,
                                          "修改素材名称", "显示名称：", initialvalue="原始显示名称"), "")

    def test_rename_cancel_and_close_return_none(self):
        def close(dialog):
            dialog.window.tk.call(dialog.window.protocol("WM_DELETE_WINDOW"))
        for action in (lambda dialog: dialog.buttons["cancel"].invoke(), close):
            self.assertIsNone(self.call_dialog(dialogs.simpledialog.askstring, action,
                                               "修改素材名称", "显示名称：", initialvalue="保留原名称"))

    def test_rename_enter_on_entry_saves_but_default_enter_cancels(self):
        def save(dialog):
            with patch.object(dialog.window, "focus_get", return_value=dialog.entry):
                dialog._enter()
        self.assertEqual(self.call_dialog(dialogs.simpledialog.askstring, save,
                                          "名称", "显示名称：", initialvalue="保留名称"), "保留名称")
        def cancel(dialog):
            with patch.object(dialog.window, "focus_get", return_value=None):
                dialog._enter()
        self.assertIsNone(self.call_dialog(dialogs.simpledialog.askstring, cancel,
                                           "名称", "显示名称：", initialvalue="保留名称"))

    def test_local_grab_is_restored_after_dialog(self):
        owner = tk.Toplevel(self.root)
        owner.geometry("200x100+12000+12000")
        owner.update()
        owner.grab_set()
        try:
            def acknowledge(dialog):
                self.assertEqual(dialog.window.grab_status(), "local")
                self.assertEqual(self.root.grab_current(), dialog.window)
                dialog.buttons["ok"].invoke()
            self.assertEqual(self.call_dialog(dialogs.messagebox.showinfo, acknowledge, "提示", "操作完成"), "ok")
            self.assertEqual(self.root.grab_current(), owner)
            self.assertEqual(owner.grab_status(), "local")
        finally:
            owner.destroy()
            owner = None

    def test_parent_destruction_exits_wait_without_callbacks(self):
        self.assertIsNone(self.call_dialog(dialogs.simpledialog.askstring,
                                           lambda dialog: self.root.destroy(), "名称", "输入名称"))

    def test_nested_modal_restores_outer_grab(self):
        def open_nested(outer):
            nested = dialogs._Dialog(outer.window, "嵌套提示", "继续？", "yesno")
            timer = self.root.after(60, nested.buttons["no"].invoke)
            try:
                self.assertIs(nested.show(), False)
                self.assertEqual(self.root.grab_current(), outer.window)
                self.assertEqual(outer.window.grab_status(), "local")
            finally:
                self.root.after_cancel(timer)
                nested = None
            outer.buttons["ok"].invoke()
        # Keep the outer fixture factory from intercepting nested construction.
        original = dialogs._Dialog
        def action(outer):
            with patch.object(dialogs, "_Dialog", original):
                open_nested(outer)
        self.assertEqual(self.call_dialog(dialogs.messagebox.showinfo, action, "提示", "外层提示"), "ok")

    def test_destroyed_parent_returns_cancel_without_native_popup(self):
        child = tk.Toplevel(self.root)
        child.destroy()
        self.assertIs(dialogs.messagebox.askyesno("授权", "继续？", parent=child), False)
        self.assertIsNone(dialogs.simpledialog.askstring("名称", "输入名称", parent=child))
        child = None

    def test_withdrawn_owner_does_not_leave_modal_wait_hidden(self):
        self.root.withdraw()
        self.assertEqual(self.call_dialog(dialogs.messagebox.showerror,
                                          lambda dialog: dialog.buttons["ok"].invoke(),
                                          "无法启动", "端口不可用"), "ok")

    def test_long_error_and_permission_text_remain_complete_and_scrollable(self):
        original = "授权后，能以当前账户读取该文件的本机程序可调用接口；这不是对某个进程身份的认证。\n" * 150
        def inspect(dialog):
            self.assertEqual(dialog.message.get("1.0", "end-1c"), original + "\n\n附加错误说明")
            self.assertEqual(dialog.message.cget("state"), "disabled")
            self.assertLess(dialog.message.yview()[1], 1)
            dialog.message.yview_moveto(1)
            self.assertAlmostEqual(dialog.message.yview()[1], 1)
            self.assertTrue(dialog.buttons["ok"].winfo_ismapped())
            self.assertGreaterEqual(dialog.buttons["ok"].winfo_width(), dialog.buttons["ok"].winfo_reqwidth())
            dialog.buttons["ok"].invoke()
        self.assertEqual(self.call_dialog(dialogs.messagebox.showerror, inspect,
                                          "操作未完成", original, detail="附加错误说明"), "ok")

    def test_native_fallback_for_early_window_failure_keeps_safe_default(self):
        with patch.object(dialogs._Dialog, "_build", side_effect=tk.TclError("window unavailable")), \
             patch.object(dialogs._native_messagebox, "askyesno", return_value=False) as native:
            self.assertIs(dialogs.messagebox.askyesno("授权", "完整权限说明", parent=self.root), False)
            native.assert_called_once_with("授权", "完整权限说明", parent=self.root, default="no")
        self.assertEqual(self.root.winfo_children(), [])

    def test_native_fallback_cleans_up_partially_created_window(self):
        bindings = self.root.bind("<Destroy>")
        with patch("surface_ui.header", side_effect=tk.TclError("early header failure")), \
             patch.object(dialogs._native_messagebox, "showerror", return_value="ok") as native:
            self.assertEqual(dialogs.messagebox.showerror("无法启动", "完整错误说明", parent=self.root), "ok")
            native.assert_called_once_with("无法启动", "完整错误说明", parent=self.root)
        self.assertEqual(self.root.winfo_children(), [])
        self.assertEqual(self.root.bind("<Destroy>"), bindings)

    def test_small_screen_high_dpi_keeps_footer_reachable(self):
        for scale in (1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                self.root.tk.call("tk", "scaling", scale * 96 / 72)
                def inspect(dialog):
                    self.assertLessEqual(dialog.window.winfo_width(), 1280)
                    self.assertLessEqual(dialog.window.winfo_height(), 720)
                    min_width, min_height = dialog.window.minsize()
                    dialog.window.geometry(f'{min_width}x{min_height}+12000+12000')
                    self.root.update()
                    self.assertGreaterEqual(dialog.message.winfo_height(), 60)
                    for button in dialog.buttons.values():
                        self.assertTrue(button.winfo_ismapped())
                        self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth() - 4)
                        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                             dialog.window.winfo_rooty() + dialog.window.winfo_height())
                    dialog.buttons["cancel"].invoke()
                with patch.object(tk.Toplevel, "winfo_screenwidth", return_value=1280), \
                     patch.object(tk.Toplevel, "winfo_screenheight", return_value=720), \
                     patch.object(tk.Toplevel, "winfo_fpixels", return_value=scale * 96):
                    self.assertIsNone(self.call_dialog(dialogs.messagebox.askyesnocancel, inspect,
                                                       "保留剪辑选区", "正文\n" * 100))

    def test_rename_minimum_retains_readable_prompt_and_input(self):
        for scale in (1, 1.5, 2):
            with self.subTest(scale=scale):
                self.root.tk.call('tk', 'scaling', scale * 96 / 72)
                def inspect(dialog):
                    width, height = dialog.window.minsize()
                    dialog.window.geometry(f'{width}x{height}+12000+12000')
                    self.root.update()
                    self.assertGreaterEqual(dialog.message.winfo_height(), 60)
                    self.assertGreaterEqual(dialog.entry.winfo_height(), dialog.entry.winfo_reqheight())
                    for button in dialog.buttons.values():
                        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                             dialog.window.winfo_rooty() + dialog.window.winfo_height())
                    dialog.buttons['cancel'].invoke()
                with patch.object(tk.Toplevel, 'winfo_screenwidth', return_value=1280), \
                     patch.object(tk.Toplevel, 'winfo_screenheight', return_value=720), \
                     patch.object(tk.Toplevel, 'winfo_fpixels', return_value=96 * scale):
                    self.assertIsNone(self.call_dialog(dialogs.simpledialog.askstring, inspect,
                                                       '修改素材名称', '显示名称（保留原文件名）：'))


if __name__ == "__main__":
    unittest.main()
