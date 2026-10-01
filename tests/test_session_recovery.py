import gc
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.app = self.root = None
        self.open()

    def open(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, smoke=True, state_dir=self.folder)

    def close(self):
        self.app.pending.clear()
        self.app.close()
        self.app = self.root = None
        gc.collect()

    def tearDown(self):
        if self.app:
            self.close()
        self.directory.cleanup()

    def test_default_enables_ai_and_rotates_pairing(self):
        self.assertTrue(self.app.ai.enabled.is_set())
        token = self.app.bridge.token
        self.close()
        self.open()
        self.assertTrue(self.app.ai.enabled.is_set())
        self.assertNotEqual(token, self.app.bridge.token)
        self.assertEqual(json.loads((self.folder / 'ai-client.json').read_text())['token'], self.app.bridge.token)

    def test_explicit_trust_restarts_with_rotated_token_and_can_revoke(self):
        self.app.revoke_trust()
        with patch('app.messagebox.askyesno', return_value=False):
            self.app.grant_trust()
        self.assertFalse(self.app.ai.enabled.is_set())
        with patch('app.messagebox.askyesno', return_value=True):
            self.app.grant_trust()
        token = self.app.bridge.token
        self.assertTrue(self.app.ai.enabled.is_set())
        self.close()
        self.open()
        self.assertTrue(self.app.ai.enabled.is_set())
        self.assertNotEqual(token, self.app.bridge.token)
        settings = json.loads((self.folder / 'ai-client.json').read_text())
        self.assertEqual(settings['token'], self.app.bridge.token)
        self.app.revoke_trust()
        self.close()
        self.open()
        self.assertFalse(self.app.ai.enabled.is_set())

    def test_expiry_closes_current_session(self):
        self.app.storage_settings['trust_until'] = time.time() - 1
        self.app.ai_enabled.set(True)
        self.app.ai.enabled.set()
        self.app._tick_once()
        self.assertFalse(self.app.ai.enabled.is_set())
        self.assertEqual(self.app.storage_settings['trust_until'], 0)

    def test_pending_receipt_becomes_interrupted_and_folder_restores(self):
        self.app.remember_folder.set(True)
        self.app.folder.set(str(self.folder / 'media'))
        self.app.save_preferences()
        ident = self.app.store.add({'url': 'http://127.0.0.1/fixture.mp4'}, manual=True)['id']
        self.app.store.update(ident, status='下载中')
        self.app.persist_receipts()
        self.close()
        self.open()
        item = self.app.store.items[ident]
        self.assertEqual(item['status'], '已中断')
        self.assertTrue(item['history'])
        self.assertEqual(item['url'], '')
        self.assertEqual(self.app.folder.get(), str(self.folder / 'media'))
        with self.assertRaises(ValueError):
            self.app.ai.dispatch('download', {'id': ident})

    def test_api_rejects_relative_paths_without_creating_them(self):
        with self.assertRaises(ValueError):
            self.app.ai.folder({'folder': 'relative-audit-folder'})
        with self.assertRaises(ValueError):
            self.app.ai.enqueue_clip({'source': 'relative.mp4', 'start': 0, 'end': 1})

    def test_settings_window_and_opt_in_log(self):
        self.app.open_preferences()
        self.root.update()
        self.assertTrue(self.app.preferences_window.winfo_exists())
        self.assertFalse((self.folder / 'diagnostics.log').exists())
        self.app.diagnostics.set(True)
        self.app.save_preferences()
        self.app.local_state.log('bridge', reason='user', token='secret', path='C:/private')
        content = (self.folder / 'diagnostics.log').read_text()
        self.assertNotIn('secret', content)
        self.assertNotIn('private', content)


if __name__ == '__main__':
    unittest.main()
