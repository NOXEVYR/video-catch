"""Consent persistence with real isolated files and no Tk/display dependency."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from app import App
from local_state import LocalState

class Value:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value

class TrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def app(self):
        app = App.__new__(App)
        app.root = Mock()
        app.local_state = LocalState(self.path)
        app.storage_settings = app.local_state.load_settings()
        app.notice = Value('')
        app.remember_folder = Value(False)
        app.folder = Value(str(self.path))
        app.diagnostics = Value(False)
        app.ai_enabled = Value(False)
        app.ai = Mock(enabled=threading.Event())
        app.bridge = Mock(token='A' * 32, server_port=18796)
        return app

    def test_invalid_preferences_do_not_prevent_revocation(self):
        app = self.app()
        with patch('app.messagebox.askyesno', return_value=True): app.grant_trust()
        app.remember_folder.set(True)
        app.folder.set('x' * 5000)
        self.assertFalse(app.save_preferences())
        self.assertEqual(app.storage_settings['folder'], '')
        app.revoke_trust()
        restarted = self.app()
        restarted._restore_trust()
        self.assertEqual(restarted.storage_settings['trust_until'], 0)
        self.assertFalse(restarted.ai.enabled.is_set())

    def test_grant_rotates_pairing_then_revoke_survives_restart(self):
        app = self.app()
        with patch('app.messagebox.askyesno', return_value=True): app.grant_trust()
        self.assertTrue(app.ai.enabled.is_set())
        restarted = self.app()
        restarted.bridge.token = 'B' * 32
        restarted._restore_trust()
        self.assertTrue(restarted.ai.enabled.is_set())
        self.assertEqual(json.loads((self.path/'ai-client.json').read_text())['token'], 'B' * 32)
        restarted.revoke_trust()
        after = self.app(); after._restore_trust()
        self.assertFalse(after.ai.enabled.is_set())

    def test_declined_or_failed_grant_does_not_enable(self):
        app = self.app()
        with patch('app.messagebox.askyesno', return_value=False): app.grant_trust()
        self.assertFalse(app.ai.enabled.is_set())
        with patch('app.messagebox.askyesno', return_value=True), patch.object(app.local_state, 'save_settings', return_value=False):
            app.grant_trust()
        self.assertFalse(app.ai.enabled.is_set())
        self.assertEqual(app.storage_settings['trust_until'], 0)

    def test_expired_grant_is_not_restored(self):
        app = self.app()
        app.storage_settings['ai_on_start'] = False
        app.storage_settings['trust_until'] = time.time() - 1
        app.local_state.save_settings(app.storage_settings)
        app._restore_trust()
        self.assertFalse(app.ai.enabled.is_set())
        self.assertEqual(self.app().storage_settings['trust_until'], 0)

    def test_failed_revoke_still_disables_current_access_and_warns(self):
        app = self.app()
        app.ai.enabled.set()
        with patch.object(app.local_state, 'save_settings', return_value=False): app.revoke_trust()
        self.assertFalse(app.ai.enabled.is_set())
        self.assertIn('撤销未能写入', app.notice.get())

    def test_revoke_refreshes_open_preferences(self):
        app = self.app()
        app.preferences_window = Mock()
        app.revoke_trust()
        app.preferences_window.refresh_status.assert_called_once()

if __name__ == '__main__': unittest.main()
