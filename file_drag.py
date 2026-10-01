"""Windows Shell file drag source. Only completed local files; COPY only."""
import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
from pathlib import Path
import struct
import sys
import uuid
import threading


class Format(c.Structure):
    _fields_ = [("cfFormat", w.WORD), ("ptd", c.c_void_p), ("dwAspect", w.DWORD), ("lindex", w.LONG), ("tymed", w.DWORD)]


class Medium(c.Structure):
    _fields_ = [("tymed", w.DWORD), ("handle", c.c_void_p), ("release", c.c_void_p)]


def method(obj, index, result, *arguments):
    table = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(result, c.c_void_p, *arguments)(table[index])


def primary_button_down():
    user = c.WinDLL('user32')
    return bool(user.GetAsyncKeyState(2 if user.GetSystemMetrics(23) else 1) & 0x8000)


@contextmanager
def file_data(paths):
    if sys.platform != "win32":
        raise RuntimeError("拖出复制目前支持 Windows，请使用打开文件夹入口")
    paths = [str(Path(path).resolve()) for path in paths]
    if not paths or len(paths) > 200 or any(not Path(path).is_file() for path in paths):
        raise ValueError("请只拖动已保存且仍存在的视频文件（每次最多 200 个）")
    ole, shell, kernel = c.OleDLL("ole32"), c.WinDLL("shell32"), c.WinDLL("kernel32")
    ole.OleInitialize.argtypes = [c.c_void_p]
    ole.OleInitialize(None)
    obj = c.c_void_p()
    try:
        shell.SHCreateDataObject.argtypes = [c.c_void_p, w.UINT, c.c_void_p, c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)]
        shell.SHCreateDataObject.restype = w.LONG
        iid = (c.c_ubyte * 16).from_buffer_copy(uuid.UUID("0000010e-0000-0000-c000-000000000046").bytes_le)
        if shell.SHCreateDataObject(None, 0, None, None, iid, c.byref(obj)) < 0:
            raise OSError("无法创建 Windows 文件拖放对象")
        payload = struct.pack('<IiiII', 20, 0, 0, 0, 1) + ('\0'.join(paths) + '\0\0').encode('utf-16-le')
        kernel.GlobalAlloc.argtypes = [w.UINT, c.c_size_t]; kernel.GlobalAlloc.restype = c.c_void_p
        kernel.GlobalLock.argtypes = [c.c_void_p]; kernel.GlobalLock.restype = c.c_void_p
        kernel.GlobalUnlock.argtypes = [c.c_void_p]
        kernel.GlobalFree.argtypes = [c.c_void_p]
        handle = kernel.GlobalAlloc(0x42, len(payload))
        if not handle: raise MemoryError()
        try:
            address = kernel.GlobalLock(handle)
            if not address: raise MemoryError()
            c.memmove(address, payload, len(payload)); kernel.GlobalUnlock(handle)
            form, medium = Format(15, None, 1, -1, 1), Medium(1, handle, None)
            code = method(obj, 7, w.LONG, c.POINTER(Format), c.POINTER(Medium), w.BOOL)(obj, c.byref(form), c.byref(medium), True)
            if code < 0: raise OSError("Windows 未接受文件拖放数据")
            handle = None  # Ownership transferred to IDataObject.
            yield obj
        finally:
            if handle: kernel.GlobalFree(handle)
    finally:
        if obj.value: method(obj, 2, w.ULONG)(obj)
        ole.OleUninitialize()


class DropSource:
    """Minimal IDropSource: cursor feedback, without Shell window snapshots."""
    def __init__(self, cancel_event=None, physical_buttons=False):
        self.refs = 1
        self.cancel_event = cancel_event
        self.physical_buttons = physical_buttons
        self.iids = {uuid.UUID(value).bytes_le for value in (
            '00000000-0000-0000-c000-000000000046',
            '00000121-0000-0000-c000-000000000046')}
        signatures = (
            (w.LONG, c.c_void_p, c.POINTER(c.c_void_p)),
            (w.ULONG,), (w.ULONG,), (w.LONG, w.BOOL, w.DWORD),
            (w.LONG, w.DWORD))
        methods = (self.query_interface, self.add_ref, self.release,
                   self.continue_drag, self.feedback)
        self.callbacks = [c.WINFUNCTYPE(sig[0], c.c_void_p, *sig[1:])(fn)
                          for sig, fn in zip(signatures, methods)]
        self.table = (c.c_void_p * 5)(*[c.cast(fn, c.c_void_p).value for fn in self.callbacks])
        self.instance = c.pointer(c.cast(self.table, c.POINTER(c.c_void_p)))
        self.pointer = c.cast(self.instance, c.c_void_p)

    def query_interface(self, _this, iid, output):
        output[0] = None
        if c.string_at(iid, 16) not in self.iids:
            return -2147467262  # E_NOINTERFACE
        output[0] = self.pointer.value
        self.refs += 1
        return 0

    def add_ref(self, _this):
        self.refs += 1
        return self.refs

    def release(self, _this):
        self.refs -= 1
        return self.refs

    def continue_drag(self, _this, escape, keys):
        if self.cancel_event is not None and self.cancel_event.is_set():
            return 0x40101
        if self.physical_buttons:
            user = c.WinDLL('user32')
            escape = bool(escape) or bool(user.GetAsyncKeyState(27) & 0x8000)
            # Keep OLE's button sample paired with the processed mouse position.
            # Replacing it with current async state can drop on an older move.
        if escape or keys & 2:
            return 0x40101  # DRAGDROP_S_CANCEL
        return 0 if keys & 1 else 0x40100  # Drop on left-button release.

    def feedback(self, _this, _effect):
        return 0x40102  # DRAGDROP_S_USEDEFAULTCURSORS


def drag_files(hwnd, paths, *, cancel_event=None, physical_buttons=False):
    # A fast release while the STA is starting must not drop at a later position.
    if physical_buttons and (not primary_button_down() or cancel_event is not None and cancel_event.is_set()):
        return False
    if physical_buttons and cancel_event is None:
        cancel_event = threading.Event()
    with file_data(paths) as obj:
        # SHDoDragDrop can capture the supplied Tk window as a drag image.
        # OLE with our own source keeps the familiar copy cursor only.
        ole = c.WinDLL("ole32")
        ole.DoDragDrop.argtypes = [c.c_void_p, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
        ole.DoDragDrop.restype = w.LONG
        source = DropSource(cancel_event, physical_buttons)  # Callbacks live throughout the native loop.
        stopped = threading.Event()
        waker = None
        wake_errors = []
        if physical_buttons:
            user = c.WinDLL('user32', use_last_error=True)
            user.GetCursorPos.argtypes = [c.POINTER(w.POINT)]
            user.GetCursorPos.restype = w.BOOL
            user.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
            user.PostThreadMessageW.restype = w.BOOL
            thread_id = c.windll.kernel32.GetCurrentThreadId()
            # Creating the queue first makes cancellation wakeups reliable even
            # when this STA has no application window or mouse input of its own.
            class Message(c.Structure):
                _fields_ = [('hwnd',w.HWND),('message',w.UINT),('wParam',w.WPARAM),
                            ('lParam',w.LPARAM),('time',w.DWORD),('pt',w.POINT),('private',w.DWORD)]
            user.PeekMessageW(c.byref(Message()),None,0,0,0)
            def wake():
                while not stopped.wait(.04):
                    primary, secondary = (2, 1) if user.GetSystemMetrics(23) else (1, 2)
                    before_button = bool(user.GetAsyncKeyState(primary) & 0x8000)
                    point = w.POINT()
                    if not user.GetCursorPos(c.byref(point)):
                        cancel_event.set()  # Never copy at an unknown/default position.
                    # OLE interprets this mouse message's position, even when it
                    # is only a wakeup. A constant (0, 0) misses the real target.
                    keys = sum(flag for key, flag in ((primary, 1), (secondary, 2),
                               (16, 4), (17, 8), (4, 16))
                               if user.GetAsyncKeyState(key) & 0x8000)
                    if before_button != bool(keys & 1) and not user.GetCursorPos(c.byref(point)):
                        cancel_event.set()
                    position = (point.x & 0xffff) | ((point.y & 0xffff) << 16)
                    if not user.PostThreadMessageW(thread_id,0x200,keys,position):
                        if not wake_errors:
                            wake_errors.append(c.get_last_error())
                        cancel_event.set()
            waker = threading.Thread(target=wake,daemon=True)
            waker.start()
        effect = w.DWORD()
        try:
            if physical_buttons and (not primary_button_down() or cancel_event is not None and cancel_event.is_set()):
                return False
            result = ole.DoDragDrop(obj, source.pointer, 1, c.byref(effect))
        finally:
            stopped.set()
            if waker is not None:
                waker.join(timeout=1)
        if result < 0: raise OSError("文件拖放失败，请重试或打开文件夹复制")
        if wake_errors:
            raise OSError(f'文件拖放消息投递失败（Windows {wake_errors[0]}），请重试或导出副本。')
        return result == 0x40100 and effect.value == 1
