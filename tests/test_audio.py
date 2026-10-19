"""Cancellable audio tests using fake PortAudio and operating system calls."""

import errno
import os
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from media.audio import AudioSource


class AudioSourceTests(unittest.TestCase):
    def setUp(self):
        self.audio = Mock()
        self.microphone = self.audio.open.return_value
        self.audio.get_device_count.return_value = 1
        self.audio.get_device_info_by_index.return_value = {
            "name": "Microphone",
            "maxInputChannels": 2,
        }
        self.pyaudio = SimpleNamespace(paInt16=8, PyAudio=Mock(return_value=self.audio))
        self.enterContext(patch.dict("sys.modules", {"pyaudio": self.pyaudio}))
        self.on_error = Mock()
        self.source = AudioSource(
            Path("test-audio.pipe"), "Microphone", lambda: None, self.on_error
        )

    def assert_audio_closed(self):
        self.microphone.stop_stream.assert_called_once()
        self.microphone.close.assert_called_once()
        self.audio.terminate.assert_called_once()

    def test_fifo_startup_is_nonblocking_and_can_be_cancelled_without_reader(self):
        attempted_open = threading.Event()

        def no_reader(path, flags):
            attempted_open.set()
            raise OSError(errno.ENXIO, "no reader")

        with (
            patch("media.audio.os.mkfifo", create=True),
            patch("media.audio.os.O_NONBLOCK", 2048, create=True),
            patch("media.audio.os.open", side_effect=no_reader) as open_pipe,
        ):
            self.source.start()
            try:
                self.assertTrue(attempted_open.wait(1))
            finally:
                self.source.stop()
            self.assertFalse(self.source._thread.is_alive())
            self.assertEqual(open_pipe.call_args.args[1], os.O_WRONLY | 2048)

        self.on_error.assert_not_called()
        self.assert_audio_closed()

    def test_fifo_permission_error_is_not_retried(self):
        with (
            patch("media.audio.os.O_NONBLOCK", 2048, create=True),
            patch(
                "media.audio.os.open",
                side_effect=PermissionError(errno.EACCES, "denied"),
            ) as open_pipe,
            self.assertRaises(PermissionError),
        ):
            self.source._open_pipe()
        open_pipe.assert_called_once()

    def test_partial_writes_and_backpressure_preserve_all_bytes(self):
        with patch("media.audio.os.write", side_effect=[BlockingIOError(), 2, 4]) as write:
            self.source._write(7, b"abcdef")

        self.assertEqual(
            [bytes(call.args[1]) for call in write.call_args_list],
            [b"abcdef", b"abcdef", b"cdef"],
        )

    def test_stop_after_capture_closes_every_audio_resource(self):
        self.microphone.read.return_value = b"pcm audio"
        with (
            patch.object(self.source, "_open_pipe", return_value=7),
            patch.object(self.source, "_write", side_effect=lambda *_: self.source._stop.set()),
            patch("media.audio.os.close") as close_pipe,
        ):
            self.source._run()

        self.microphone.read.assert_called_once_with(1024, exception_on_overflow=False)
        close_pipe.assert_called_once_with(7)
        self.on_error.assert_not_called()
        self.assert_audio_closed()

    def test_capture_failure_reports_error_and_closes_resources(self):
        self.microphone.read.side_effect = OSError("microphone disconnected")
        with (
            patch.object(self.source, "_open_pipe", return_value=7),
            patch("media.audio.os.close") as close_pipe,
        ):
            self.source._run()

        self.on_error.assert_called_once()
        close_pipe.assert_called_once_with(7)
        self.assert_audio_closed()

    def test_missing_pyaudio_reports_error(self):
        with patch.dict("sys.modules", {"pyaudio": None}):
            self.source._run()
        self.on_error.assert_called_once()
        self.pyaudio.PyAudio.assert_not_called()

    def test_microphone_stop_failure_still_closes_device_and_portaudio(self):
        self.microphone.read.side_effect = OSError("microphone disconnected")
        self.microphone.stop_stream.side_effect = OSError("device already stopped")
        with (
            patch.object(self.source, "_open_pipe", return_value=7),
            patch("media.audio.os.close"),
        ):
            self.source._run()
        self.assert_audio_closed()


if __name__ == "__main__":
    unittest.main()
