"""Native local Windows pipe integration; no microphone, FFmpeg or network."""

import ctypes
import os
import threading
import time
import unittest
import uuid
from ctypes import wintypes

from media.windows_pipe import WindowsAudioPipe


@unittest.skipUnless(os.name == "nt", "Windows named pipe integration")
class WindowsAudioPipeTests(unittest.TestCase):
    def setUp(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self.kernel.ReadFile.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.stop = threading.Event()
        self.pipe = WindowsAudioPipe(rf"\\.\pipe\streaming-test-{uuid.uuid4().hex}", self.stop)
        self.addCleanup(self.pipe.close)

    def open_reader(self):
        reader = self.kernel.CreateFileW(self.pipe.path, 0x80000000, 0, None, 3, 0, None)
        if reader == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.addCleanup(self.kernel.CloseHandle, reader)
        return reader

    def test_early_reader_connects_and_receives_exact_pcm_bytes(self):
        # CreateFile before ConnectNamedPipe exercises ERROR_PIPE_CONNECTED.
        reader = self.open_reader()
        self.assertTrue(self.pipe.connect(timeout=1))
        payload = bytes(range(256)) * 16
        self.pipe.write(payload[:1536])
        self.pipe.write(payload[1536:])
        buffer = ctypes.create_string_buffer(len(payload))
        received = wintypes.DWORD()
        self.assertTrue(
            self.kernel.ReadFile(reader, buffer, len(payload), ctypes.byref(received), None)
        )
        self.assertEqual(buffer.raw[: received.value], payload)

    def test_connection_without_reader_can_be_cancelled(self):
        results = []
        worker = threading.Thread(target=lambda: results.append(self.pipe.connect()), daemon=True)
        worker.start()
        self.assertTrue(worker.is_alive())
        self.pipe.cancel()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.pipe.close()
        self.pipe.close()

    def test_blocked_write_can_be_cancelled_when_reader_stops_consuming(self):
        self.open_reader()
        self.assertTrue(self.pipe.connect(timeout=1))
        completed = threading.Event()
        failures = []

        def write_large_payload():
            try:
                self.pipe.write(bytes(1024 * 1024), timeout=5)
            except Exception as error:
                failures.append(error)
            finally:
                completed.set()

        worker = threading.Thread(target=write_large_payload, daemon=True)
        worker.start()
        self.assertFalse(completed.wait(0.05))
        self.pipe.cancel()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(completed.is_set())
        self.assertEqual(failures, [])

    def test_connect_timeout_cleans_pending_operation(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.pipe.connect(timeout=0.03)
        self.assertLess(time.monotonic() - started, 1)
        # The cancelled operation no longer owns a handle or OVERLAPPED buffer.
        self.pipe.close()
        self.assertIsNone(self.pipe._handle)


if __name__ == "__main__":
    unittest.main()
