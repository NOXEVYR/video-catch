import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clip_drafts import ClipDrafts


class DraftTests(unittest.TestCase):
    def test_restart_restore_and_no_source_path_in_private_file(self):
        with tempfile.TemporaryDirectory() as folder:
            drafts = ClipDrafts(folder)
            source = str(Path(folder) / '私人视频.mp4')
            selection = ((123, 456, 789), 2, 8, 3)
            self.assertTrue(drafts.save(source, selection))
            self.assertEqual(ClipDrafts(folder).get(source), selection)
            self.assertNotIn(source, drafts.path.read_text())
            self.assertTrue(drafts.save(source))
            self.assertIsNone(ClipDrafts(folder).get(source))

    def test_failed_write_rolls_back_and_corrupt_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            drafts = ClipDrafts(folder)
            selection = ((123, 456, 789), 2, 8, 3)
            with patch('clip_drafts.os.replace', side_effect=PermissionError):
                self.assertFalse(drafts.save(Path(folder) / 'v.mp4', selection))
            self.assertFalse(drafts.entries)
            self.assertFalse(list(Path(folder).glob('.clip-draft-*')))
            drafts.path.write_bytes(b'broken')
            protected = ClipDrafts(folder)
            self.assertFalse(protected.save(Path(folder) / 'v.mp4', selection))
            self.assertEqual(drafts.path.read_bytes(), b'broken')

    def test_memory_mode_is_bounded_and_never_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            drafts = ClipDrafts(folder, enabled=False)
            for i in range(40):
                self.assertTrue(drafts.save(Path(folder) / f'{i}.mp4', ((1, 2, 3), 0, 1, 0)))
            self.assertEqual(len(drafts.entries), 32)
            self.assertFalse(drafts.path.exists())
