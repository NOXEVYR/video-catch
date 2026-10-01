"""Workbench controls remain reachable at common small-display DPI settings."""
import gc
from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


class SmallDisplayTests(unittest.TestCase):
    def test_empty_and_populated_workbench_at_three_scales(self):
        for scale in (1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                root = tk.Tk()
                root.tk.call("tk", "scaling", scale * 96 / 72)
                app = None
                try:
                    with patch.object(root, "winfo_screenwidth", return_value=1280), \
                         patch.object(root, "winfo_screenheight", return_value=720), \
                         patch.object(root, "winfo_fpixels", return_value=scale * 96):
                        app = App(root, smoke=True)
                    app.show_page('browser')
                    callback_errors = []
                    root.report_callback_exception = lambda *error: callback_errors.append(error)
                    root.update()
                    self.assertEqual(app.workbench_canvas.xview(), (0.0, 1.0))
                    self.assertNotIn("hgrip", str(__import__('tkinter.ttk', fromlist=['Style']).Style(root).layout('Horizontal.Sash')))
                    self.assertNotIn("vgrip", str(__import__('tkinter.ttk', fromlist=['Style']).Style(root).layout('Vertical.Sash')))
                    before = app.source_split.sashpos(0)
                    app.source_split.sashpos(0, before + 20)
                    self.assertNotEqual(app.source_split.sashpos(0), before)
                    app.source_split.sashpos(0, before)
                    self.assertGreaterEqual(app.media.winfo_height(), 100)
                    self.assertLess(app.workbench_canvas.yview()[1], 1)
                    app.workbench_canvas.yview_moveto(1)
                    self.assertAlmostEqual(app.workbench_canvas.yview()[1], 1)
                    app.url.event_generate("<FocusIn>")
                    root.update()
                    self.assertLess(app.workbench_canvas.yview()[0], .3)
                    app.toggle_download_settings()
                    root.update()
                    def check_buttons(widget):
                        for child in widget.winfo_children():
                            if child.winfo_class() == "TButton":
                                self.assertTrue(child.winfo_ismapped(), child.cget("text"))
                                self.assertGreaterEqual(child.winfo_width(), child.winfo_reqwidth() - 4,
                                                        child.cget("text"))
                            check_buttons(child)
                    check_buttons(app.pages["browser"])
                    self.assertEqual(callback_errors, [])
                    item = app.store.add({"url": "http://127.0.0.1/fixture.mp4"}, manual=True)
                    app.refresh()
                    root.update()
                    self.assertTrue(app.media.exists(item["id"]))
                    self.assertFalse(app.empty_media.winfo_ismapped())
                    check_buttons(app.pages["browser"])
                finally:
                    if app:
                        app.close()
                    else:
                        root.destroy()
                    app = root = None
                    gc.collect()


if __name__ == "__main__":
    unittest.main()
