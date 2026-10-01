"""Per-user/session Windows instance ownership and activation without a socket.

The primary owns a named mutex and polls an auto-reset event on its Tk thread.
A secondary only signals that event; the owner restores its existing window.
Objects have a DACL restricted to the current user SID. No AI endpoint or token
is involved. acquire()/close() must be called on the same thread (mutex owner).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
import time


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL)]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


class SingleInstance:
    def __init__(self, app_id="VideoCatch.Startup.v1"):
        if not app_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in app_id):
            raise ValueError("app_id must contain only ASCII letters, digits, dots and hyphens")
        self.app_id = app_id
        self.primary = False
        self._mutex = self._event = None
        self._acquired = False
        if sys.platform == "win32":
            self._load_windows()

    def _load_windows(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        signatures = {
            "CreateMutexW": ([ctypes.POINTER(_SecurityAttributes), wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "CreateEventW": ([ctypes.POINTER(_SecurityAttributes), wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenEventW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "ReleaseMutex": ([wintypes.HANDLE], wintypes.BOOL),
            "SetEvent": ([wintypes.HANDLE], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "GetCurrentProcess": ([], wintypes.HANDLE),
            "ProcessIdToSessionId": ([wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "LocalFree": ([wintypes.LPVOID], wintypes.LPVOID),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.kernel, name)
            fn.argtypes, fn.restype = args, result
        self.advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.advapi.OpenProcessToken.restype = wintypes.BOOL
        self.advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.advapi.GetTokenInformation.restype = wintypes.BOOL
        self.advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        self.advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
        self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL

    def _scope(self):
        token = wintypes.HANDLE()
        if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            length = wintypes.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
            if not length.value:
                raise ctypes.WinError(ctypes.get_last_error())
            data = ctypes.create_string_buffer(length.value)
            if not self.advapi.GetTokenInformation(token, 1, data, length, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid_ptr = ctypes.cast(data, ctypes.POINTER(_SidAndAttributes)).contents.Sid
            sid_text = wintypes.LPWSTR()
            if not self.advapi.ConvertSidToStringSidW(sid_ptr, ctypes.byref(sid_text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                sid = sid_text.value
            finally:
                self.kernel.LocalFree(ctypes.cast(sid_text, wintypes.LPVOID))
        finally:
            self.kernel.CloseHandle(token)
        session = wintypes.DWORD()
        if not self.kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise ctypes.WinError(ctypes.get_last_error())
        return sid, session.value

    def acquire(self):
        """Return True for primary, False for secondary; errors are never ownership."""
        if self._acquired:
            return self.primary
        if sys.platform != "win32":
            self.primary = self._acquired = True
            return True
        sid, session = self._scope()
        prefix = f"Local\\{self.app_id}.{sid}.{session}"
        self._event_name = prefix + ".activate"
        security = wintypes.LPVOID()
        if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"D:P(A;;GA;;;{sid})", 1, ctypes.byref(security), None):
            raise ctypes.WinError(ctypes.get_last_error())
        attrs = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), security, False)
        try:
            ctypes.set_last_error(0)
            mutex = self.kernel.CreateMutexW(ctypes.byref(attrs), True, prefix + ".owner")
            error = ctypes.get_last_error()
            if not mutex:
                raise ctypes.WinError(error)
            if error == 183:  # ERROR_ALREADY_EXISTS: initial ownership was ignored.
                self.kernel.CloseHandle(mutex)
                self._acquired = True
                return False
            self._mutex = mutex
            self._event = self.kernel.CreateEventW(ctypes.byref(attrs), False, False, self._event_name)
            if not self._event:
                error = ctypes.get_last_error()
                self.close()
                raise ctypes.WinError(error)
            # Discard a stale signal if another handle briefly survived a prior owner.
            self.kernel.WaitForSingleObject(self._event, 0)
            self.primary = self._acquired = True
            return True
        finally:
            self.kernel.LocalFree(security)

    def wake_existing(self, timeout_ms=1500):
        """Signal the owner; False means no delivery and should be reported to user.

        Bounded retry covers the primary's mutex/event creation race. Call before
        making the secondary Tk root, so it cannot show a second startup animation.
        """
        if sys.platform != "win32" or not self._acquired or self.primary:
            return False
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while True:
            event = self.kernel.OpenEventW(0x0002, False, self._event_name)
            if event:
                try:
                    return bool(self.kernel.SetEvent(event))
                finally:
                    self.kernel.CloseHandle(event)
            if ctypes.get_last_error() != 2 or time.monotonic() >= deadline:
                return False
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def poll(self):
        """Consume one activation request; call on Tk thread then app.show_main()."""
        if not self.primary or not self._event:
            return False
        result = self.kernel.WaitForSingleObject(self._event, 0)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def close(self):
        if self._event:
            self.kernel.CloseHandle(self._event)
            self._event = None
        if self._mutex:
            self.kernel.ReleaseMutex(self._mutex)
            self.kernel.CloseHandle(self._mutex)
            self._mutex = None
        self.primary = self._acquired = False
