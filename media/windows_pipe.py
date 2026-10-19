"""Local Windows byte pipe with cancellable, overlapped connection and writes."""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from functools import lru_cache

ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995
ERROR_PIPE_CONNECTED = 535
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


@lru_cache(maxsize=1)
def _kernel32():
    if os.name != "nt":
        raise OSError("Windows named pipes require Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle, dword, boolean = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    pointer = ctypes.POINTER(_Overlapped)
    signatures = {
        "CreateNamedPipeW": (
            handle,
            [wintypes.LPCWSTR, dword, dword, dword, dword, dword, dword, ctypes.c_void_p],
        ),
        "CreateEventW": (handle, [ctypes.c_void_p, boolean, boolean, wintypes.LPCWSTR]),
        "ConnectNamedPipe": (boolean, [handle, pointer]),
        "WriteFile": (boolean, [handle, ctypes.c_void_p, dword, ctypes.POINTER(dword), pointer]),
        "GetOverlappedResult": (boolean, [handle, pointer, ctypes.POINTER(dword), boolean]),
        "CancelIoEx": (boolean, [handle, pointer]),
        "WaitForSingleObject": (dword, [handle, dword]),
        "DisconnectNamedPipe": (boolean, [handle]),
        "CloseHandle": (boolean, [handle]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(kernel, name)
        function.restype = result
        function.argtypes = arguments
    return kernel


class WindowsAudioPipe:
    """One local FFmpeg reader; endpoint creation never waits for the reader."""

    def __init__(self, path: str, stop_event: threading.Event) -> None:
        if not path.startswith("\\\\.\\pipe\\"):
            raise ValueError("A local Windows named pipe path is required")
        self.path = path
        self._stop = stop_event
        self._kernel = _kernel32()
        self._operation_lock = threading.Lock()
        self._handle_lock = threading.Lock()
        # OUTBOUND | OVERLAPPED; byte mode, blocking semantics, local clients only.
        self._handle = self._kernel.CreateNamedPipeW(
            path, 0x00000002 | 0x40000000, 0x00000008, 1, 65536, 0, 0, None
        )
        if self._handle == INVALID_HANDLE_VALUE:
            self._handle = None
            raise ctypes.WinError(ctypes.get_last_error())

    def _perform(self, begin, timeout: float, *, connecting: bool = False) -> int | None:
        with self._operation_lock:
            if self._stop.is_set() or self._handle is None:
                return None
            event = self._kernel.CreateEventW(None, True, False, None)
            if not event:
                raise ctypes.WinError(ctypes.get_last_error())
            operation = _Overlapped(hEvent=event)
            transferred = wintypes.DWORD()
            pending = False
            try:
                if begin(ctypes.byref(operation), ctypes.byref(transferred)):
                    return transferred.value
                error = ctypes.get_last_error()
                # The client can open the endpoint before ConnectNamedPipe runs.
                if connecting and error == ERROR_PIPE_CONNECTED:
                    return 0
                if error != ERROR_IO_PENDING:
                    raise ctypes.WinError(error)
                pending = True
                deadline = time.monotonic() + timeout
                while not self._stop.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("The audio pipe did not respond in time")
                    status = self._kernel.WaitForSingleObject(
                        event, max(1, min(50, int(remaining * 1000)))
                    )
                    if status == WAIT_OBJECT_0:
                        pending = False
                        if self._kernel.GetOverlappedResult(
                            self._handle, ctypes.byref(operation), ctypes.byref(transferred), False
                        ):
                            return transferred.value
                        error = ctypes.get_last_error()
                        if error == ERROR_OPERATION_ABORTED and self._stop.is_set():
                            return None
                        raise ctypes.WinError(error)
                    if status != WAIT_TIMEOUT:
                        raise ctypes.WinError(ctypes.get_last_error())
                return None
            finally:
                if pending:
                    self._kernel.CancelIoEx(self._handle, ctypes.byref(operation))
                    # The operation and its write buffer must outlive cancellation.
                    self._kernel.GetOverlappedResult(
                        self._handle, ctypes.byref(operation), ctypes.byref(transferred), True
                    )
                self._kernel.CloseHandle(event)

    def connect(self, timeout: float = 30) -> bool:
        return (
            self._perform(
                lambda operation, _: self._kernel.ConnectNamedPipe(self._handle, operation),
                timeout,
                connecting=True,
            )
            is not None
        )

    def write(self, data: bytes, timeout: float = 5) -> None:
        remaining = memoryview(data)
        while remaining and not self._stop.is_set():
            buffer = ctypes.create_string_buffer(remaining.tobytes())
            size = len(remaining)
            written = self._perform(
                lambda operation, count, buffer=buffer, size=size: self._kernel.WriteFile(
                    self._handle, buffer, size, count, operation
                ),
                timeout,
            )
            if written is None:
                return
            if not written:
                raise BrokenPipeError("The audio pipe stopped accepting data")
            remaining = remaining[written:]

    def cancel(self) -> None:
        self._stop.set()
        with self._handle_lock:
            if self._handle is not None:
                self._kernel.CancelIoEx(self._handle, None)

    def close(self) -> None:
        self.cancel()
        with self._operation_lock, self._handle_lock:
            handle, self._handle = self._handle, None
            if handle is not None:
                self._kernel.DisconnectNamedPipe(handle)
                self._kernel.CloseHandle(handle)
