"""Collaboration callbacks and real Tk layout, with no private App state."""
import gc
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import weakref

from ai_collaboration_ui import local_api_address, show_collaboration
from surface_ui import register_styles
from ui import BG, FG


class AddressTests(unittest.TestCase):
    def test_only_valid_loopback_port_is_displayed(self):
        for port, expected in ((19301, 19301), (True, 18796), (0, 18796),
                               (65536, 18796), ("private-token", 18796)):
            with self.subTest(port=port):
                app = SimpleNamespace(bridge=SimpleNamespace(server_port=port))
                self.assertEqual(local_api_address(app), f"http://127.0.0.1:{expected}/api/v1/")
        self.assertEqual(local_api_address(SimpleNamespace()), "http://127.0.0.1:18796/api/v1/")


class PrivateBridge:
    server_port = 19301

    @property
    def token(self):
        raise AssertionError("The panel must never read pairing secrets")


class CollaborationWidgetTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.geometry("1000x800+12000+12000")
            self.root.withdraw()
        except tk.TclError:
            self.skipTest("Tk display not available")
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 9), background=BG, foreground=FG)
        register_styles(self.root)
        self.app = SimpleNamespace(root=self.root, ai_enabled=tk.BooleanVar(self.root, False),
                                   bridge=PrivateBridge(), ai_collaboration_window=None,
                                   toggle_ai=Mock(), copy_ai_collaboration=Mock(),
                                   copy_pairing=Mock(), open_preferences=Mock())
        self.window = None

    def show(self):
        # A transient Toplevel needs a mapped owner for real layout measurement.
        self.root.deiconify()
        self.window = show_collaboration(self.app)
        # Exercise physical 650 px width even if the desktop DPI minimum grows.
        self.window.minsize(650, 560)
        self.window.geometry("650x560+12000+12000")
        self.root.update()
        return self.window

    def tearDown(self):
        if self.window is not None:
            self.window.close()
        if self.root is not None:
            self.root.destroy()
        errors = self.errors
        self.window = self.app = self.root = None
        gc.collect()
        self.assertEqual(errors, [])

    def test_open_and_reuse_are_nonmodal_and_do_not_copy(self):
        window = self.show()
        self.assertEqual(window.title(), "拾影 · AI 协作")
        self.assertIsNone(window.grab_current())
        self.assertFalse(window.attributes("-topmost"))
        self.assertIs(show_collaboration(self.app), window)
        self.app.copy_ai_collaboration.assert_not_called()
        self.app.copy_pairing.assert_not_called()
        self.app.toggle_ai.assert_not_called()

    def test_buttons_delegate_authorization_and_clipboard_callbacks(self):
        window = self.show()
        window.toggle_button.invoke()
        self.assertTrue(self.app.ai_enabled.get())
        self.app.toggle_ai.assert_called_once_with()
        window.toggle_button.invoke()
        self.assertFalse(self.app.ai_enabled.get())
        self.assertEqual(self.app.toggle_ai.call_count, 2)
        window.copy_guide_button.invoke()
        # App enables only after a successful copy; the panel must not pre-enable.
        self.assertFalse(self.app.ai_enabled.get())
        window.copy_pairing_button.invoke()
        window.preferences_button.invoke()
        self.app.copy_ai_collaboration.assert_called_once_with()
        self.app.copy_pairing.assert_called_once_with()
        self.app.open_preferences.assert_called_once_with(section="AI 协作")

    def test_external_status_changes_are_immediate(self):
        window = self.show()
        self.app.ai_enabled.set(True)
        self.assertEqual(window.status.get(), "协作已开启")
        self.assertIn("撤销", window.toggle_button.cget("text"))
        self.app.ai_enabled.set(False)
        self.assertEqual(window.status.get(), "协作已关闭")

    def test_close_removes_trace_and_reopen_is_fresh(self):
        window = self.show()
        self.assertEqual(len(self.app.ai_enabled.trace_info()), 1)
        window.close()
        window.close()
        self.assertIsNone(self.app.ai_collaboration_window)
        self.assertEqual(self.app.ai_enabled.trace_info(), [])
        self.assertIsNone(window.app)
        self.assertIsNone(window.body)
        replacement = self.show()
        self.assertIsNot(replacement, window)
        self.assertEqual(len(self.app.ai_enabled.trace_info()), 1)

    def test_repeated_close_releases_window_and_installs_no_timer(self):
        before = self.root.tk.call("after", "info")
        references = []
        for _ in range(3):
            window = self.show()
            references.append(weakref.ref(window))
            window.close()
            self.window = window = None
            gc.collect()
        self.assertTrue(all(reference() is None for reference in references))
        self.assertEqual(self.root.tk.call("after", "info"), before)
        self.assertEqual(self.app.ai_enabled.trace_info(), [])

    def test_escape_closes_and_owner_destroy_cleans_up(self):
        window = self.show()
        # Invoke the registered Escape binding without taking desktop focus.
        window.tk.call(window._escape_binding)
        self.root.update()
        self.assertIsNone(self.app.ai_collaboration_window)
        self.show()
        self.root.destroy()
        self.root = None
        self.assertIsNone(self.app.ai_collaboration_window)
        self.assertEqual(self.app.ai_enabled.trace_info(), [])

    def test_small_and_high_dpi_layout_has_no_clipped_controls(self):
        for scaling in (1.0, 1.5, 2.0):
            self.root.tk.call("tk", "scaling", scaling * 96 / 72)
            window = self.show()
            for size in ("650x560", "1000x800"):
                with self.subTest(scaling=scaling, size=size):
                    window.geometry(size + "+12000+12000")
                    self.root.update()
                    self.assertEqual(window.winfo_width(), int(size.split("x")[0]))
                    self.assertEqual(window.body.canvas.xview(), (0.0, 1.0))
                    for button in (window.toggle_button, window.copy_guide_button,
                                   window.copy_pairing_button, window.preferences_button):
                        self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth() - 2)
                        self.assertLessEqual(button.winfo_x() + button.winfo_width(), button.master.winfo_width())
                        # Focus navigation reveals each control without desktop focus.
                        button.event_generate("<FocusIn>")
                        self.root.update()
                        y = button.winfo_rooty() - window.body.frame.winfo_rooty()
                        top = window.body.canvas.canvasy(0)
                        self.assertGreaterEqual(y, top - 1)
                        self.assertLessEqual(y + button.winfo_height(), top + window.body.canvas.winfo_height() + 1)
                    window.body.canvas.yview_moveto(1)
                    self.root.update()
                    self.assertAlmostEqual(window.body.canvas.yview()[1], 1)
            window.close()
            self.window = None
            gc.collect()


if __name__ == "__main__":
    unittest.main()
