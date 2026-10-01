"""File actions use only generated TemporaryDirectory assets."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from file_actions import capture_identity, export_copies, recycle_files, FileActionError


class ExportTests(unittest.TestCase):
    def test_collision_preserves_empty_original_and_exports_unique_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'sample.mp4'
            source.write_bytes(b'original')
            target = root / 'out'
            target.mkdir()
            (target / source.name).write_bytes(b'')
            result = export_copies([str(source)], target)
            self.assertTrue(result[0].ok)
            self.assertEqual((target / source.name).read_bytes(), b'')
            self.assertEqual((target / 'sample (1).mp4').read_bytes(), b'original')
            self.assertEqual(source.read_bytes(), b'original')

    def test_nonzero_partial_copy_is_removed_and_originals_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'sample.png'
            source.write_bytes(b'original')
            target = root / 'out'
            target.mkdir()
            (target / source.name).write_bytes(b'')
            def fail(stream, output, size):
                output.write(b'partial')
                raise OSError('fixture disk full')
            with patch('file_actions.shutil.copyfileobj', fail):
                result = export_copies([str(source)], target)
            self.assertFalse(result[0].ok)
            self.assertEqual(list(target.iterdir()), [target / source.name])
            self.assertEqual((target / source.name).read_bytes(), b'')
            self.assertEqual(source.read_bytes(), b'original')

    def test_saturated_filenames_do_not_delete_preexisting_empty_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'sample.mp4'
            source.write_bytes(b'original')
            target = root / 'out'
            target.mkdir()
            for index in range(1000):
                (target / ('sample.mp4' if index == 0 else f'sample ({index}).mp4')).touch()
            result = export_copies([str(source)], target)
            self.assertFalse(result[0].ok)
            self.assertEqual(len(list(target.iterdir())), 1000)

    def test_failed_copy_does_not_unlink_external_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'sample.mp4'
            source.write_bytes(b'original')
            target = root / 'out'
            target.mkdir()
            # On Windows a live output handle prevents replacement; simulate an
            # identity mismatch at cleanup to verify conservative ownership.
            stat_original = Path.stat
            def mismatch(path, *args, **kwargs):
                info = stat_original(path, *args, **kwargs)
                if path == target / source.name:
                    return type('Info', (), {'st_dev': info.st_dev, 'st_ino': info.st_ino + 1})()
                return info
            with patch('file_actions.shutil.copyfileobj', side_effect=OSError('fixture')), patch('file_actions.Path.stat', mismatch):
                self.assertFalse(export_copies([str(source)], target)[0].ok)
            self.assertTrue((target / source.name).exists())


@unittest.skipUnless(sys.platform == 'win32', 'Windows local fixed drive')
class RecycleTests(unittest.TestCase):
    def test_busy_and_changed_files_never_call_recycler(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'sample.mp4'
            source.write_bytes(b'fixture')
            identity = capture_identity(source)
            with patch('file_actions._shell_recycle') as recycler:
                self.assertFalse(recycle_files([identity], busy=lambda path: True)[0].ok)
                source.write_bytes(b'changed')
                self.assertFalse(recycle_files([identity])[0].ok)
                recycler.assert_not_called()
            self.assertEqual(source.read_bytes(), b'changed')

    def test_disappearance_without_shell_receipt_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'sample.png'
            source.write_bytes(b'fixture')
            identity = capture_identity(source)
            moved = Path(temp) / 'moved.png'
            def unconfirmed(path):
                Path(path).rename(moved)
            result = recycle_files([identity], recycler=unconfirmed)
            self.assertFalse(result[0].ok)
            self.assertEqual(moved.read_bytes(), b'fixture')

    def test_native_windows_confirms_generated_asset_entered_recycle_bin(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'videocatch-recycle-fixture.png'
            source.write_bytes(b'generated recycle test asset')
            result = recycle_files([capture_identity(source)])
            self.assertTrue(result[0].ok, result[0].error)
            self.assertFalse(source.exists())

    def test_shell_failure_preserves_file_without_delete_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'sample.mp4'
            source.write_bytes(b'fixture')
            with patch('file_actions._shell_recycle', side_effect=FileActionError('fixture denied')):
                result = recycle_files([capture_identity(source)])
            self.assertFalse(result[0].ok)
            self.assertEqual(source.read_bytes(), b'fixture')


if __name__ == '__main__':
    unittest.main()
