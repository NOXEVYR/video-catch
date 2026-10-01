"""Shared-menu integration and safe group actions with generated local PNGs."""
import gc
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import App


class LibraryContextMenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='videocatch-menu-fixture-')
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, smoke=True)
        self.root.geometry('1020x800+12000+12000')
        self.root.deiconify()
        self.gallery = self.app.library_window
        self.keys = []
        for index in range(3):
            path = Path(self.temp.name) / f'generated-{index}.png'
            photo = tk.PhotoImage(master=self.root, width=2, height=2)
            photo.put('#ffc18e', to=(0,0,2,2))
            photo.write(str(path), format='png')
            self.app.library.add(path, source='screenshot')
            self.keys.append(self.app.library.key(path))
        self.gallery.render()
        self.root.update()
        self.area = patch('context_menu.owner_work_area', new=lambda _: (12000,12000,13280,12800))
        self.area.start()

    def tearDown(self):
        self.app.close()
        self.area.stop()
        self.app = self.gallery = self.root = None
        gc.collect()
        self.temp.cleanup()

    def show(self, key):
        self.gallery.show_context_menu(key, SimpleNamespace(x_root=12100,y_root=12100))
        self.root.update()
        return self.gallery.context_menu

    def test_right_click_selected_card_preserves_group_and_other_card_replaces(self):
        g = self.gallery
        for key in self.keys[:2]:
            g.selection[key].set(True)
        name = g.cards[self.keys[0]].winfo_children()[1]
        name.event_generate('<Button-3>', x=5,y=5)
        self.root.update()
        self.assertEqual(set(g.selected_keys()), set(self.keys[:2]))
        self.assertIs(self.root.grab_current(), g.context_menu.window)
        self.assertEqual(str(g.context_menu.buttons[2]['state']), 'disabled')
        g.context_menu.close()
        self.show(self.keys[2])
        self.assertEqual(g.selected_keys(), [self.keys[2]])

    def test_group_remove_closes_menu_before_callback_and_keeps_actual_files(self):
        g = self.gallery
        for key in self.keys[:2]:
            g.selection[key].set(True)
        observed = []
        original = g.remove_selected
        def dispatch():
            observed.append((set(g.selected_keys()), g.context_menu.window, self.root.grab_current()))
            original()
        with patch.object(g, 'remove_selected', new=dispatch):
            menu = self.show(self.keys[0])
            menu.buttons[-1].invoke()
        self.assertEqual(observed, [(set(self.keys[:2]),None,None)])
        for key in self.keys[:2]:
            self.assertTrue(self.app.library.entries[key]['hidden'])
            self.assertTrue(Path(self.app.library.entries[key]['path']).is_file())
        self.assertFalse(self.app.library.entries[self.keys[2]]['hidden'])

    def test_rebuild_and_page_switch_release_menu_grab(self):
        self.show(self.keys[0])
        self.app.library.entries[self.keys[0]]['name'] = '更新显示名称'
        self.gallery.render()
        self.assertIsNone(self.gallery.context_menu.window)
        self.assertIsNone(self.root.grab_current())
        self.show(self.keys[0])
        self.app.show_page('browser')
        self.assertIsNone(self.gallery.context_menu.window)
        self.assertIsNone(self.root.grab_current())

    def test_active_marquee_is_ended_before_context_menu(self):
        g = self.gallery
        with patch('library_selection._pointer_snapshot', return_value=None):
            g.begin_marquee(SimpleNamespace(x_root=g.canvas.winfo_rootx()+5,
                                           y_root=g.canvas.winfo_rooty()+5,state=0))
            self.assertIsNotNone(g.marquee)
            self.show(self.keys[0])
        self.assertIsNone(g.marquee)
        self.assertIsNone(g._marquee_timer)
        g.context_menu.close()
        self.assertIsNone(self.root.grab_current())

    def test_library_close_destroys_menu_and_releases_capture(self):
        menu = self.show(self.keys[0])
        popup = menu.window
        self.gallery.close()
        self.assertIsNone(menu.window)
        self.assertFalse(popup.winfo_exists())
        self.assertIsNone(self.root.grab_current())


if __name__ == '__main__':
    unittest.main()
