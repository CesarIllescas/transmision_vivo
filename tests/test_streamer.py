"""Process ownership and failure handling without opening real streams."""

import io
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from classes.streamer import Streamer


def make_process():
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    process.stdin = Mock()
    process.stdout = None
    process.stderr = None
    return process


class StreamerTests(unittest.TestCase):
    def setUp(self):
        self.streamer = Streamer("video", "audio", 30, ["ffmpeg", "-i", "-"])
        self.process = make_process()
        self.popen_patch = patch("classes.streamer.subprocess.Popen", return_value=self.process)
        self.popen = self.popen_patch.start()
        self.addCleanup(self.popen_patch.stop)
        self.addCleanup(self.streamer.stop_streaming)

    def test_start_is_idempotent_and_unread_output_cannot_fill_a_pipe(self):
        self.assertIs(self.streamer.start_streaming(), self.process)
        self.assertIs(self.streamer.start_streaming(), self.process)
        self.popen.assert_called_once()
        self.assertEqual(self.popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(self.popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(self.streamer.is_streaming())

    def test_failed_start_does_not_leave_stream_marked_running(self):
        self.popen.side_effect = FileNotFoundError("ffmpeg")
        with self.assertRaises(FileNotFoundError):
            self.streamer.start_streaming()
        self.assertFalse(self.streamer.is_streaming())
        self.streamer.stop_streaming()

    def test_write_requires_a_live_process_and_flushes_one_frame(self):
        with self.assertRaises(BrokenPipeError):
            self.streamer.write(b"frame")
        self.streamer.start_streaming()
        self.streamer.write(b"frame")
        self.process.stdin.write.assert_called_once_with(b"frame")
        self.process.stdin.flush.assert_called_once_with()
        self.process.poll.return_value = 1
        self.assertFalse(self.streamer.is_streaming())
        with self.assertRaises(BrokenPipeError):
            self.streamer.write(b"another frame")

    def test_broken_pipe_cleans_up_the_process(self):
        self.streamer.start_streaming()
        self.process.stdin.write.side_effect = BrokenPipeError("closed")
        with self.assertRaises(BrokenPipeError):
            self.streamer.write(b"frame")
        self.assertIsNone(self.streamer.process)
        self.process.terminate.assert_called_once()
        self.process.stdin.close.assert_called_once()

    def test_stop_is_idempotent_and_reaps_process(self):
        self.streamer.start_streaming()
        self.streamer.stop_streaming(timeout=1)
        self.streamer.stop_streaming(timeout=1)
        self.process.terminate.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=1)
        self.process.stdin.close.assert_called_once()
        self.assertFalse(self.streamer.is_streaming())

    def test_stop_kills_a_process_that_ignores_termination(self):
        self.streamer.start_streaming()
        self.process.wait.side_effect = [subprocess.TimeoutExpired("ffmpeg", 1), 0]
        self.streamer.stop_streaming(timeout=1)
        self.process.kill.assert_called_once()
        self.assertEqual(self.process.wait.call_count, 2)
        self.assertFalse(self.streamer.is_streaming())

    def test_restart_reaps_the_exited_process_before_replacing_it(self):
        self.streamer.start_streaming()
        self.process.poll.return_value = 1
        replacement = make_process()
        self.popen.return_value = replacement
        self.assertIs(self.streamer.start_streaming(), replacement)
        self.process.wait.assert_called_once()
        self.process.stdin.close.assert_called_once()
        self.process.terminate.assert_not_called()

    def test_diagnostics_are_drained_and_invalid_utf8_is_tolerated(self):
        received = []
        self.process.stderr = io.BytesIO(b"first line\n\ninvalid \xff\n")
        self.streamer._on_stderr = received.append
        self.streamer.start_streaming()
        self.streamer._stderr_thread.join(timeout=1)
        self.assertEqual(received, ["first line", "invalid \ufffd"])
        self.streamer.stop_streaming(timeout=1)
        self.assertTrue(self.process.stderr.closed)

    def test_callback_failure_does_not_stop_diagnostics_reader(self):
        callback = Mock(side_effect=[ValueError("UI error"), None])
        self.process.stderr = io.BytesIO(b"first\nsecond\n")
        self.streamer._on_stderr = callback
        with self.assertLogs("classes.streamer", level="ERROR"):
            self.streamer.start_streaming()
            self.streamer._stderr_thread.join(timeout=1)
        self.assertEqual(callback.call_count, 2)

    def test_stop_can_terminate_a_process_while_a_frame_write_is_blocked(self):
        entered = threading.Event()
        released = threading.Event()

        def blocked_write(_data):
            entered.set()
            if not released.wait(timeout=2):
                raise TimeoutError("Stop did not release the writer")

        self.process.stdin.write.side_effect = blocked_write
        self.process.terminate.side_effect = released.set
        self.streamer.start_streaming()
        writer = threading.Thread(target=self.streamer.write, args=(b"frame",), daemon=True)
        writer.start()
        self.assertTrue(entered.wait(timeout=1))
        self.streamer.stop_streaming(timeout=1)
        writer.join(timeout=1)
        self.assertFalse(writer.is_alive())
        self.assertTrue(released.is_set())


if __name__ == "__main__":
    unittest.main()
