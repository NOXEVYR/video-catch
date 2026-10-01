import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import threading
from file_drag import file_data, method, Format, Medium, DropSource, drag_files


@unittest.skipUnless(sys.platform == 'win32', 'Windows Shell required')
class DragTests(unittest.TestCase):
    def test_cursor_only_source_com_contract(self):
        source = DropSource()
        output = c.c_void_p()
        for name in ('00000000-0000-0000-c000-000000000046', '00000121-0000-0000-c000-000000000046'):
            iid = (c.c_ubyte * 16).from_buffer_copy(uuid.UUID(name).bytes_le)
            query = method(source.pointer, 0, w.LONG, c.c_void_p, c.POINTER(c.c_void_p))
            self.assertEqual(query(source.pointer, iid, c.byref(output)), 0)
            self.assertEqual(output.value, source.pointer.value)
            method(output, 2, w.ULONG)(output)
        unknown = (c.c_ubyte * 16)()
        self.assertLess(query(source.pointer, unknown, c.byref(output)), 0)
        self.assertIsNone(output.value)
        continued = method(source.pointer, 3, w.LONG, w.BOOL, w.DWORD)
        self.assertEqual(continued(source.pointer, False, 1), 0)
        self.assertEqual(continued(source.pointer, False, 0), 0x40100)
        self.assertEqual(continued(source.pointer, True, 1), 0x40101)
        self.assertEqual(continued(source.pointer, False, 3), 0x40101)
        self.assertEqual(method(source.pointer, 4, w.LONG, w.DWORD)(source.pointer, 1), 0x40102)
        self.assertEqual(source.refs, 1)

    def test_real_ole_cancel_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '拖出测试.mp4'
            path.write_bytes(b'fixture')
            # OLE waits for a mouse message before polling QueryContinueDrag.
            # Post only to this test thread, without moving the user's pointer.
            thread_id = c.windll.kernel32.GetCurrentThreadId()
            timer = threading.Timer(.1, lambda: c.windll.user32.PostThreadMessageW(thread_id, 0x200, 0, 0))
            with patch.object(DropSource, 'continue_drag', return_value=0x40101):
                timer.start()
                try:
                    self.assertFalse(drag_files(0, [path]))
                finally:
                    timer.cancel()
                    timer.join()
            self.assertEqual(path.read_bytes(), b'fixture')

    def test_native_shell_object_contains_unicode_file_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/'测试片段一.mp4', Path(directory)/'second clip.mp4']
            for path in paths: path.write_bytes(b'fixture')
            with file_data(paths) as obj:
                form, medium = Format(15, None, 1, -1, 1), Medium()
                result = method(obj, 3, w.LONG, c.POINTER(Format), c.POINTER(Medium))(obj, c.byref(form), c.byref(medium))
                self.assertEqual(result, 0)
                try:
                    shell = c.WinDLL('shell32')
                    shell.DragQueryFileW.argtypes = [c.c_void_p, w.UINT, w.LPWSTR, w.UINT]
                    self.assertEqual(shell.DragQueryFileW(medium.handle, 0xffffffff, None, 0), 2)
                    for index, path in enumerate(paths):
                        value = c.create_unicode_buffer(32768)
                        shell.DragQueryFileW(medium.handle, index, value, len(value))
                        self.assertEqual(value.value, str(path.resolve()))
                finally:
                    ole = c.OleDLL('ole32')
                    ole.ReleaseStgMedium.argtypes = [c.POINTER(Medium)]
                    ole.ReleaseStgMedium(c.byref(medium))
            self.assertTrue(all(path.exists() for path in paths))

    def test_unavailable_source_is_rejected(self):
        with self.assertRaises(ValueError):
            with file_data(['missing-source.mp4']): pass

    def test_short_escape_pulse_and_ole_button_sample_are_preserved(self):
        user = unittest.mock.Mock()
        user.GetAsyncKeyState.return_value = 0
        with patch('file_drag.c.WinDLL', return_value=user):
            source = DropSource(physical_buttons=True)
            # Physical button may already be up, but this move's sample is held.
            self.assertEqual(source.continue_drag(None, False, 1), 0)
            user.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key == 1 else 0
            # A new physical press cannot rewrite a processed release sample.
            self.assertEqual(source.continue_drag(None, False, 0), 0x40100)
            # OLE saw Esc, even if the physical key was released before callback.
            self.assertEqual(source.continue_drag(None, True, 1), 0x40101)
            self.assertEqual(source.continue_drag(None, False, 3), 0x40101)

if __name__ == '__main__': unittest.main()
