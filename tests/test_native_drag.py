from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from native_drag import NativeFileDrag
from file_drag import drag_files


@unittest.skipUnless(sys.platform=='win32','Windows OLE required')
class NativeDragTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'原始素材.png'
        self.path.write_bytes(b'fixture')
        self.session=NativeFileDrag()

    def tearDown(self):
        self.session.cancel()
        deadline=time.monotonic()+2
        while self.session.busy and time.monotonic()<deadline:
            self.session.poll()
            time.sleep(.01)
        self.assertFalse(self.session.busy)
        self.temp.cleanup()

    def wait(self):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            result=self.session.poll()
            if result is not None: return result
            time.sleep(.01)
        self.fail('Native drag did not finish')

    def test_sta_owns_immutable_paths_and_returns_without_blocking_ui(self):
        entered=threading.Event()
        threads=[]
        paths=[self.path]
        def native(_hwnd,snapshot,**options):
            threads.append(threading.get_ident())
            entered.set()
            options['cancel_event'].wait(1)
            self.assertEqual(snapshot,(str(self.path.resolve()),))
            return False
        with patch('file_drag.drag_files',side_effect=native):
            self.session.start(paths)
            self.assertTrue(entered.wait(1))
            paths.clear()
            self.assertNotEqual(threads[0],threading.get_ident())
            self.assertTrue(self.session.busy)
            with self.assertRaises(RuntimeError): self.session.start([self.path])
            self.session.cancel()
            self.assertEqual(self.wait(),(False,''))
        self.assertTrue(self.path.exists())

    def test_worker_error_is_delivered_and_next_drag_is_possible(self):
        with patch('file_drag.drag_files',side_effect=OSError('fixture error')):
            self.session.start([self.path])
            self.assertEqual(self.wait(),(False,'fixture error'))
        with patch('file_drag.primary_button_down',return_value=False):
            self.session.start([self.path])
            self.assertEqual(self.wait(),(False,''))

    def test_actual_ole_cancel_is_woken_on_worker_thread(self):
        with patch('file_drag.primary_button_down',return_value=True):
            self.session.start([self.path])
            self.session.cancel()
            self.assertEqual(self.wait(),(False,''))
        self.assertTrue(self.path.exists())

    def test_fast_release_does_not_enter_native_loop(self):
        with patch('file_drag.primary_button_down',return_value=False), patch('file_drag.file_data') as data:
            self.assertFalse(drag_files(0,[self.path],physical_buttons=True))
            data.assert_not_called()

    def test_unavailable_source_cannot_start_worker(self):
        with self.assertRaises(ValueError): self.session.start([self.path.with_suffix('.missing')])
        self.assertFalse(self.session.busy)

    def test_release_during_com_setup_never_enters_drop_loop(self):
        from file_drag import file_data
        with patch('file_drag.primary_button_down', side_effect=[True, False]), \
             patch('file_drag.file_data', wraps=file_data) as data:
            self.assertFalse(drag_files(0, [self.path], physical_buttons=True))
            self.assertTrue(data.called)
            # The actual late-release path also joins its wakeup thread.
        self.assertTrue(self.path.exists())

    def test_wakeup_carries_current_cursor_including_negative_monitor_origin(self):
        import ctypes as c
        from ctypes import wintypes as w
        original_dll = c.WinDLL
        for position in ((1380, 520), (-1200, -70)):
            with self.subTest(position=position):
                user, ole = Mock(), Mock()
                user.GetSystemMetrics.return_value = 0
                user.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key in (1, 16, 17) else 0
                posted = threading.Event()
                messages = []
                def cursor(pointer):
                    point = c.cast(pointer, c.POINTER(w.POINT)).contents
                    point.x, point.y = position
                    return True
                def post(_thread, message, keys, packed):
                    messages.append((message, keys, packed))
                    posted.set()
                    return True
                def drop(*_args):
                    self.assertTrue(posted.wait(1))
                    return 0x40101
                user.GetCursorPos.side_effect = cursor
                user.PostThreadMessageW.side_effect = post
                ole.DoDragDrop.side_effect = drop
                def dll(name, **_options):
                    return user if name == 'user32' else ole if name == 'ole32' else original_dll(name)
                with patch('file_drag.c.WinDLL', side_effect=dll), \
                     patch('file_drag.primary_button_down', return_value=True):
                    self.assertFalse(drag_files(0, [self.path], physical_buttons=True))
                message, keys, packed = messages[0]
                self.assertEqual(message, 0x200)
                self.assertEqual(keys, 1 | 4 | 8)
                self.assertEqual((c.c_short(packed & 0xffff).value,
                                  c.c_short((packed >> 16) & 0xffff).value), position)

    def test_unknown_cursor_position_cancels_instead_of_dropping_at_zero(self):
        import ctypes as c
        original_dll = c.WinDLL
        user, ole = Mock(), Mock()
        user.GetSystemMetrics.return_value = 0
        user.GetAsyncKeyState.return_value = 0
        user.GetCursorPos.return_value = False
        posted = threading.Event()
        cancelled = threading.Event()
        user.PostThreadMessageW.side_effect = lambda *_: posted.set() or True
        def drop(*_args):
            self.assertTrue(posted.wait(1))
            self.assertTrue(cancelled.is_set())
            return 0x40101
        ole.DoDragDrop.side_effect = drop
        def dll(name, **_options):
            return user if name == 'user32' else ole if name == 'ole32' else original_dll(name)
        with patch('file_drag.c.WinDLL', side_effect=dll), \
             patch('file_drag.primary_button_down', return_value=True):
            self.assertFalse(drag_files(0, [self.path], cancel_event=cancelled, physical_buttons=True))

    def test_failed_message_delivery_cancels_and_reports_failure(self):
        import ctypes as c
        original_dll = c.WinDLL
        user, ole = Mock(), Mock()
        user.GetSystemMetrics.return_value = 0
        user.GetAsyncKeyState.return_value = 0
        user.GetCursorPos.return_value = True
        posted, cancelled = threading.Event(), threading.Event()
        user.PostThreadMessageW.side_effect = lambda *_: posted.set() and False
        def drop(*_args):
            self.assertTrue(posted.wait(1))
            self.assertTrue(cancelled.is_set())
            return 0x40101
        ole.DoDragDrop.side_effect = drop
        def dll(name, **_options):
            return user if name == 'user32' else ole if name == 'ole32' else original_dll(name)
        with patch('file_drag.c.WinDLL', side_effect=dll), \
             patch('file_drag.primary_button_down', return_value=True), \
             self.assertRaisesRegex(OSError, '消息投递失败'):
            drag_files(0, [self.path], cancel_event=cancelled, physical_buttons=True)
        self.assertTrue(self.path.exists())


@unittest.skipUnless(sys.platform == 'win32', 'Windows OLE required')
class NativeDragLifecycleTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from app import App
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / '源素材.png'
        self.path.write_bytes(b'fixture')
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, smoke=True)
        self.entered = threading.Event()
        def blocked(_hwnd, _paths, **options):
            self.entered.set()
            options['cancel_event'].wait(2)
            return False
        self.native = patch('file_drag.drag_files', side_effect=blocked)
        self.native.start()

    def tearDown(self):
        import gc
        if self.app.native_drag_active:
            self.app.cancel_native_drag()
            self.finish()
        if not self.app.closing:
            self.app.close()
        self.native.stop()
        self.app = self.root = None
        gc.collect()
        self.temp.cleanup()

    def begin(self):
        self.entered.clear()
        self.assertTrue(self.app.start_native_drag([self.path]))
        self.assertTrue(self.entered.wait(1))
        self.assertTrue(self.app.native_drag_active)

    def finish(self):
        deadline = time.monotonic() + 3
        while self.app.native_drag_active and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.native_drag_active)

    def test_page_changes_cancel_in_both_directions_and_allow_next_drag(self):
        self.begin()
        self.app.show_page('browser')
        self.finish()
        self.begin()
        self.app.show_page('library')
        self.finish()
        self.assertTrue(self.path.exists())

    def test_source_busy_and_completion_reveal_wait_until_drag_ends(self):
        self.begin()
        self.assertTrue(self.app.media_path_busy(self.path))
        self.assertFalse(self.app.media_path_busy(self.path.with_suffix('.other')))
        with patch.object(self.app, 'open_library') as opened:
            self.app.queue_completed_media('fixture', str(self.path))
            self.app.present_completed_media()
            opened.assert_not_called()
            self.app.cancel_native_drag()
            self.finish()
            self.app.present_completed_media()
            opened.assert_called_once_with(str(self.path))
        self.assertFalse(self.app.media_path_busy(self.path))

    def test_exit_waits_for_sta_cleanup_before_destroying_root(self):
        self.begin()
        self.app.close()
        self.assertTrue(self.app._exit_after_drag)
        self.assertFalse(self.app.closing)
        self.finish()
        self.assertTrue(self.app.closing)
        self.assertTrue(self.path.exists())

    def test_direct_gallery_close_cancels_drag(self):
        self.begin()
        self.app.library_window.close()
        self.finish()
        self.assertTrue(self.path.exists())


if __name__=='__main__': unittest.main()
