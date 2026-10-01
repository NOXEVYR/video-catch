"""Bounded local file actions. Recycle never falls back to permanent deletion."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid


class FileActionError(ValueError):
    pass


@dataclass(frozen=True)
class FileIdentity:
    path: str
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class FileResult:
    path: str
    ok: bool
    error: str = ''


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _safe_file(path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or '\x00' in str(candidate):
        raise FileActionError('只接受已有本机文件的绝对路径')
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        if _is_link(current):
            raise FileActionError('路径包含联接或符号链接，拒绝移动原文件')
    if not candidate.is_file() or not stat.S_ISREG(candidate.stat().st_mode):
        raise FileActionError('文件不存在或不是普通文件')
    return candidate


def _local_recycle_drive(path: Path):
    if sys.platform != 'win32' or not path.drive or str(path).startswith('\\\\'):
        raise FileActionError('仅支持 Windows 本机固定磁盘的回收站')
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [w.LPCWSTR]
    kernel.GetDriveTypeW.restype = w.UINT
    if kernel.GetDriveTypeW(path.drive + '\\') != 3:  # DRIVE_FIXED
        raise FileActionError('此磁盘不可确认支持回收站，原文件未删除')


def capture_identity(path) -> FileIdentity:
    """Capture immediately before confirmation; compare again before recycle."""
    candidate = _safe_file(path)
    _local_recycle_drive(candidate)
    details = candidate.stat()
    if not details.st_ino:
        raise FileActionError('无法确认文件身份，原文件未删除')
    return FileIdentity(str(candidate), details.st_dev, details.st_ino,
                        details.st_size, details.st_mtime_ns)


def _same_file(identity: FileIdentity) -> bool:
    try:
        current = _safe_file(identity.path).stat()
    except (OSError, FileActionError):
        return False
    return (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) == (
        identity.device, identity.inode, identity.size, identity.modified_ns)


class GUID(c.Structure):
    _fields_ = [('data1', w.DWORD), ('data2', w.WORD), ('data3', w.WORD), ('data4', c.c_ubyte * 8)]


def _guid(value: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(obj, index, result, *arguments):
    table = c.cast(obj, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(result, c.c_void_p, *arguments)(table[index])


def _check_hresult(code, message):
    if code < 0:
        raise FileActionError(message + f'（Windows 错误 0x{code & 0xffffffff:08X}）')


class _RecycleSink:
    """Keep callback objects alive until PerformOperations returns.

    PostDeleteItem confirms the actual operation and its Recycle Bin item:
    https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperationprogresssink-postdeleteitem
    """
    def __init__(self):
        self.confirmed = False
        self.seen = False
        self.callbacks = []
        self.table = (c.c_void_p * 19)()
        self.object = (c.c_void_p * 1)(c.cast(self.table, c.c_void_p))
        self.pointer = c.cast(self.object, c.c_void_p)
        sink_iid = uuid.UUID('04b0f1a7-9490-44bc-96e1-4296a31252e2').bytes_le
        unknown_iid = uuid.UUID('00000000-0000-0000-c000-000000000046').bytes_le

        def query(this, iid, result):
            if c.string_at(iid, 16) in (sink_iid, unknown_iid):
                result[0] = self.pointer.value
                return 0
            result[0] = None
            return -2147467262  # E_NOINTERFACE

        def post_delete(this, flags, item, outcome, recycled_item):
            self.seen = True
            self.confirmed = outcome >= 0 and bool(recycled_item)
            return 0

        specs = [
            (w.LONG, (c.POINTER(GUID), c.POINTER(c.c_void_p)), query),
            (w.ULONG, (), lambda this: 2), (w.ULONG, (), lambda this: 1),
            (w.LONG, (), None), (w.LONG, (w.LONG,), None),
            (w.LONG, (w.DWORD, c.c_void_p, w.LPCWSTR), None),
            (w.LONG, (w.DWORD, c.c_void_p, w.LPCWSTR, w.LONG, c.c_void_p), None),
            (w.LONG, (w.DWORD, c.c_void_p, c.c_void_p, w.LPCWSTR), None),
            (w.LONG, (w.DWORD, c.c_void_p, c.c_void_p, w.LPCWSTR, w.LONG, c.c_void_p), None),
            (w.LONG, (w.DWORD, c.c_void_p, c.c_void_p, w.LPCWSTR), None),
            (w.LONG, (w.DWORD, c.c_void_p, c.c_void_p, w.LPCWSTR, w.LONG, c.c_void_p), None),
            (w.LONG, (w.DWORD, c.c_void_p), None),
            (w.LONG, (w.DWORD, c.c_void_p, w.LONG, c.c_void_p), post_delete),
            (w.LONG, (w.DWORD, c.c_void_p, w.LPCWSTR), None),
            (w.LONG, (w.DWORD, c.c_void_p, w.LPCWSTR, w.LPCWSTR, w.DWORD, w.LONG, c.c_void_p), None),
            (w.LONG, (w.UINT, w.UINT), None),
            (w.LONG, (), None), (w.LONG, (), None), (w.LONG, (), None),
        ]
        for index, (result, args, callback) in enumerate(specs):
            wrapped = c.WINFUNCTYPE(result, c.c_void_p, *args)(callback or (lambda *args: 0))
            self.callbacks.append(wrapped)
            self.table[index] = c.cast(wrapped, c.c_void_p).value


def _shell_recycle(path: str) -> bool:
    """IFileOperation with FOFX_RECYCLEONDELETE; no DeleteFile fallback."""
    ole = c.WinDLL('ole32')
    shell = c.WinDLL('shell32')
    ole.CoInitializeEx.argtypes = [c.c_void_p, w.DWORD]
    ole.CoInitializeEx.restype = w.LONG
    ole.CoCreateInstance.argtypes = [c.POINTER(GUID), c.c_void_p, w.DWORD, c.POINTER(GUID), c.POINTER(c.c_void_p)]
    ole.CoCreateInstance.restype = w.LONG
    ole.CoUninitialize.argtypes = []
    shell.SHCreateItemFromParsingName.argtypes = [w.LPCWSTR, c.c_void_p, c.POINTER(GUID), c.POINTER(c.c_void_p)]
    shell.SHCreateItemFromParsingName.restype = w.LONG
    code = ole.CoInitializeEx(None, 2)  # STA; no operation if the thread cannot enter STA.
    _check_hresult(code, '无法初始化 Windows 回收站接口')
    operation, item = c.c_void_p(), c.c_void_p()
    try:
        clsid = _guid('3ad05575-8857-4850-9277-11b85bdb8e09')
        iid_operation = _guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
        _check_hresult(ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid_operation), c.byref(operation)),
                       '无法创建 Windows 回收站操作')
        # RECYCLEONDELETE is the decisive flag. No permanent-delete API is called.
        flags = 0x00080000 | 0x00100000 | 0x0040 | 0x0010 | 0x0400 | 0x0004 | 0x1000 | 0x2000
        _check_hresult(_method(operation, 5, w.LONG, w.DWORD)(operation, flags), '无法设置回收站模式')
        iid_item = _guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')
        _check_hresult(shell.SHCreateItemFromParsingName(path, None, c.byref(iid_item), c.byref(item)),
                       '无法定位待回收文件')
        sink = _RecycleSink()
        _check_hresult(_method(operation, 18, w.LONG, c.c_void_p, c.c_void_p)(operation, item, sink.pointer),
                       '无法提交回收站任务')
        _check_hresult(_method(operation, 21, w.LONG)(operation), 'Windows 回收站拒绝文件')
        aborted = w.BOOL()
        _check_hresult(_method(operation, 22, w.LONG, c.POINTER(w.BOOL))(operation, c.byref(aborted)),
                       '无法确认回收站结果')
        if aborted.value:
            raise FileActionError('回收站操作已中止，原文件可能仍在原位置')
        if not sink.seen or not sink.confirmed:
            raise FileActionError('Windows 未确认文件进入回收站，请检查原文件和回收站')
        return True
    finally:
        if item.value:
            _method(item, 2, w.ULONG)(item)
        if operation.value:
            _method(operation, 2, w.ULONG)(operation)
        ole.CoUninitialize()


def recycle_files(identities, *, busy=None, recycler=None, on_result=None) -> list[FileResult]:
    """Per-item result; caller handles confirmation and catalogue journal."""
    if len(identities) > 200:
        raise FileActionError('每次最多删除 200 个素材')
    recycler = recycler or _shell_recycle
    results = []
    for identity in identities:
        path = identity.path
        try:
            if busy and busy(path):
                raise FileActionError('文件正在录制、保存、导出或剪辑，暂不能删除')
            if not _same_file(identity):
                raise FileActionError('文件在确认后已变化，原文件未删除')
            if recycler(path) is not True:
                raise FileActionError('回收站没有返回确认结果，保留素材记录')
            if Path(path).exists():
                raise FileActionError('回收站未确认文件移走，仍保留在原位置')
            result = FileResult(path, True)
        except (OSError, FileActionError, RuntimeError) as error:
            result = FileResult(path, False, str(error))
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def export_copies(paths, directory) -> list[FileResult]:
    """Copy to a chosen folder without overwriting or moving any source."""
    target_dir = Path(directory)
    if not target_dir.is_dir():
        raise FileActionError('请选择已有的导出文件夹')
    results = []
    for path in paths[:200]:
        destination = None
        created = False
        owned_identity = None
        try:
            source = _safe_file(path)
            for index in range(1000):
                name = source.name if index == 0 else f'{source.stem} ({index}){source.suffix}'
                destination = target_dir / name
                try:
                    output = destination.open('xb')
                except FileExistsError:
                    continue
                created = True
                with output:
                    details = os.fstat(output.fileno())
                    owned_identity = (details.st_dev, details.st_ino)
                    with source.open('rb') as stream:
                        shutil.copyfileobj(stream, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                break
            else:
                raise FileActionError('目标文件名已占满')
            results.append(FileResult(str(destination), True))
        except (OSError, FileActionError) as error:
            if created and destination is not None:
                try:
                    details = destination.stat()
                    if owned_identity == (details.st_dev, details.st_ino):
                        destination.unlink()
                except OSError:
                    pass
            results.append(FileResult(str(path), False, str(error)))
    return results
