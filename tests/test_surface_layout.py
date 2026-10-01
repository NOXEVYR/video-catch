"""Popup monitor placement and usable preview at small high-DPI sizes."""
import gc
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from surface_ui import setup_window


class SurfaceLayoutTests(unittest.TestCase):
    def test_popup_preserves_negative_monitor_absolute_position(self):
        root = tk.Tk()
        window = None
        try:
            root.geometry('800x600+-1800+12000')
            root.update_idletasks()
            window = tk.Toplevel(root)
            with patch('surface_ui.owner_work_area', return_value=(-1920, 12000, 0, 13080)):
                setup_window(window, None, '副屏夹具', 560, 400)
            root.update_idletasks()
            self.assertLess(window.winfo_rootx(), 0)
            self.assertGreaterEqual(window.winfo_rootx(), -1920)
            self.assertLessEqual(window.winfo_rootx() + window.winfo_width(), 0)
            self.assertGreater(window.winfo_rooty(), 10000)
        finally:
            root.destroy()
            window = root = None
            gc.collect()

    def test_small_high_dpi_clip_retains_preview_and_scrollable_controls(self):
        from app import App
        from clip_ui import ClipEditor
        class IdleDecoder:
            def __init__(self, _source): pass
            def close(self): pass
        for scale in (1, 1.5, 2):
            with self.subTest(scale=scale), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / ('很长的合成素材名称 ' * 12 + '.mp4')
                source.write_bytes(b'generated stamp fixture')
                root = tk.Tk()
                root.withdraw()
                root.tk.call('tk', 'scaling', scale * 96 / 72)
                app = editor = None
                try:
                    app = App(root, smoke=True)
                    root.geometry('1280x720+12000+12000')
                    root.deiconify()
                    with patch('clip_ui.Decoder', IdleDecoder), patch.object(ClipEditor, 'worker'), \
                         patch.object(tk.Toplevel, 'winfo_screenwidth', return_value=1280), \
                         patch.object(tk.Toplevel, 'winfo_screenheight', return_value=720), \
                         patch.object(tk.Toplevel, 'winfo_fpixels', return_value=96 * scale):
                        editor = ClipEditor(app, source)
                        app.clip_windows.append(editor)
                        min_width, min_height = editor.window.minsize()
                        editor.window.geometry(f'{min_width}x{min_height}+12000+12000')
                        root.update()
                        self.assertGreaterEqual(editor.canvas.winfo_height(), 110)
                        self.assertTrue(editor.export_button.winfo_ismapped())
                        self.assertLessEqual(editor.export_button.winfo_rooty() + editor.export_button.winfo_height(),
                                             editor.window.winfo_rooty() + editor.window.winfo_height())
                        viewport = editor.controls_body
                        self.assertGreater(viewport.frame.winfo_height(), viewport.canvas.winfo_height())
                        viewport.canvas.yview_moveto(1)
                        root.update()
                        self.assertAlmostEqual(viewport.canvas.yview()[1], 1, places=2)
                        editor.close(prompt=False)
                        self.assertTrue(viewport._closed)
                        viewport = None
                finally:
                    if app is not None:
                        app.close()
                    else:
                        root.destroy()
                    app = editor = root = None
                    gc.collect()
