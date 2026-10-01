from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collaboration import collaboration_prompt, settings_path
from runtime_paths import distribution_root


class RuntimePathsTests(unittest.TestCase):
    def test_mac_bundle_finds_sibling_client_and_quotes_shell_path(self):
        with tempfile.TemporaryDirectory(prefix="VideoCatch O'Brien ") as folder:
            root = Path(folder)
            exe = root / 'VideoCatch.app/Contents/MacOS/VideoCatch'
            exe.parent.mkdir(parents=True)
            exe.touch()
            (root/'VideoCatchAI').touch()
            with patch.object(sys, 'platform', 'darwin'), patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)):
                self.assertEqual(distribution_root(), root.resolve())
                prompt = collaboration_prompt()
                self.assertIn('zsh/bash', prompt)
                self.assertIn('VideoCatchAI', prompt)
                self.assertNotIn('VideoCatchAI.exe', prompt)
                self.assertIn("'\"'\"'", prompt)

    def test_mac_pairing_uses_application_support(self):
        with patch.object(sys, 'platform', 'darwin'), patch.dict(os.environ, {}, clear=True), patch('pathlib.Path.home', return_value=Path('/fixture')):
            self.assertEqual(settings_path(), Path('/fixture/Library/Application Support/VideoCatch/ai-client.json'))

    def test_windows_folder_layout_keeps_client_beside_exe(self):
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(Path.cwd()/'fixture/VideoCatch.exe')):
            self.assertEqual(distribution_root(), Path.cwd()/'fixture')

    def test_mac_pairing_ignores_windows_environment(self):
        from local_state import default_data_dir
        with patch.object(sys, 'platform', 'darwin'), patch.dict(os.environ, {'LOCALAPPDATA': '/wrong'}, clear=True), patch('pathlib.Path.home', return_value=Path('/fixture')):
            self.assertEqual(settings_path().parent, default_data_dir())

    def test_handoff_includes_isolated_data_directory(self):
        with tempfile.TemporaryDirectory(prefix="state O'Brien ") as folder:
            with patch.dict(os.environ, {'VIDEOCATCH_DATA_DIR': folder}):
                prompt = collaboration_prompt()
            self.assertIn('--data-dir', prompt)
            self.assertIn(str(Path(folder).resolve()).replace("'", "''"), prompt)


if __name__ == '__main__':
    unittest.main()
