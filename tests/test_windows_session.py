"""Windows session regressions without capture devices, FFmpeg, or network access."""

import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from media.curtain import CurtainVideo
from media.session import StreamingSession, StreamSettings


class WindowsStreamingSessionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("media.session.platform.system", return_value="Windows"))
        self.streamer_class = self.enterContext(patch("media.session.Streamer"))
        self.streamer = self.streamer_class.return_value
        self.streamer.is_streaming.return_value = True
        self.audio_class = self.enterContext(patch("media.session.AudioSource"))
        self.audio_class.channels = 2
        self.audio_class.rate = 44100
        self.audio = self.audio_class.return_value
        self.audio.pipe_path = r"\\.\pipe\windows-session-test"
        self.clip_class = self.enterContext(patch("media.session.CurtainVideo"))
        self.clip = self.clip_class.return_value
        self.clip.duration = 2.5
        self.session = StreamingSession()
        self.addCleanup(self.session.close)
        self.session.select_camera("0")
        self.session.select_curtain("clip.mp4")
        self.settings = StreamSettings("Microphone", (("Youtube", "test-key"),), use_curtains=True)

    def test_windows_curtain_start_normalizes_audio_and_uses_named_pipe(self):
        with patch.object(self.session, "_extract_curtain_audio") as extract:
            self.assertTrue(self.session.toggle_curtain())
            self.session.start_stream(self.settings)

        command = extract.call_args.args[0]
        self.assertEqual(command[command.index("-map") + 1], "0:a:0")
        self.assertEqual(command[command.index("-c:a") + 1], "pcm_s16le")
        self.assertEqual(command[command.index("-ac") + 1], "2")
        self.assertEqual(command[command.index("-ar") + 1], "44100")
        self.assertEqual(command[command.index("-f") + 1], "wav")
        self.assertIn(self.audio.pipe_path, self.streamer_class.call_args.args[3])
        self.assertEqual(
            self.audio_class.call_args.kwargs["curtain_position"],
            self.session._curtain_position,
        )
        self.audio.start.assert_called_once()
        self.streamer.start_streaming.assert_called_once()
        self.assertTrue(self.session.is_streaming)
        self.assertFalse(self.session.toggle_curtain())
        self.assertTrue(self.session.toggle_curtain())

    def start_in_background(self):
        failures = []

        def start():
            try:
                self.session.start_stream(self.settings)
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=start, daemon=True)
        worker.start()
        return worker, failures

    def close_and_join(self, worker):
        closer = threading.Thread(target=self.session.close, daemon=True)
        closer.start()
        worker.join(timeout=2)
        closer.join(timeout=2)
        self.assertFalse(worker.is_alive(), "Stream startup did not stop after closing")
        self.assertFalse(closer.is_alive(), "Close waited indefinitely for stream startup")

    def test_close_during_extraction_kills_process_and_never_starts_stream(self):
        extraction_started = threading.Event()
        process = Mock()
        process.returncode = None
        process.poll.side_effect = lambda: process.returncode
        process.kill.side_effect = lambda: setattr(process, "returncode", -9)

        def enter_process():
            extraction_started.set()
            return process

        with patch("media.session.subprocess.Popen") as popen:
            popen.return_value.__enter__.side_effect = enter_process
            worker, failures = self.start_in_background()
            self.assertTrue(extraction_started.wait(timeout=1))
            directory = Path(self.session._temporary.name)

            self.close_and_join(worker)

        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValueError)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        self.streamer_class.assert_not_called()
        self.audio_class.assert_not_called()
        self.assertFalse(directory.exists())
        self.assertIsNone(self.session._temporary)
        self.assertFalse(self.session.is_streaming)

    def test_close_during_streamer_start_releases_partially_started_resources(self):
        startup_started = threading.Event()

        def start_streamer():
            startup_started.set()
            self.assertTrue(self.session._closed.wait(timeout=1))

        self.streamer.start_streaming.side_effect = start_streamer
        with patch.object(self.session, "_extract_curtain_audio"):
            worker, failures = self.start_in_background()
            self.assertTrue(startup_started.wait(timeout=1))
            directory = Path(self.session._temporary.name)

            self.close_and_join(worker)

        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValueError)
        self.streamer.stop_streaming.assert_called_once()
        self.audio.stop.assert_called_once()
        self.assertFalse(directory.exists())
        self.assertFalse(self.session.is_streaming)

    def test_closed_session_rejects_start_before_opening_resources(self):
        self.session.close()
        with patch.object(self.session, "_extract_curtain_audio") as extract:
            with self.assertRaises(ValueError):
                self.session.start_stream(self.settings)

        extract.assert_not_called()
        self.streamer_class.assert_not_called()
        self.audio_class.assert_not_called()

    def test_close_while_constructing_streamer_prevents_process_start(self):
        def construct_streamer(*args, **kwargs):
            self.session._closed.set()
            return self.streamer

        self.streamer_class.side_effect = construct_streamer
        with patch.object(self.session, "_extract_curtain_audio"):
            with self.assertRaises(ValueError):
                self.session.start_stream(self.settings)

        self.streamer.start_streaming.assert_not_called()
        self.streamer.stop_streaming.assert_called_once()
        self.audio.stop.assert_called_once()
        self.assertIsNone(self.session._temporary)

    def test_failed_extraction_cleans_temporary_directory(self):
        directories = []

        def fail_extraction(command):
            directories.append(Path(command[-1]).parent)
            raise subprocess.CalledProcessError(1, command)

        with patch.object(self.session, "_extract_curtain_audio", side_effect=fail_extraction):
            with self.assertRaisesRegex(ValueError, "contiene audio"):
                self.session.start_stream(self.settings)

        self.assertFalse(directories[0].exists())
        self.assertIsNone(self.session._temporary)
        self.streamer_class.assert_not_called()
        self.audio_class.assert_not_called()

    def test_scene_audio_loops_on_video_clock_and_restarts_after_switching(self):
        self.session._curtain_audio = Path("virtual.wav")
        self.session._curtain_duration = 2.5
        with patch("media.session.time.monotonic", return_value=10.0):
            self.assertTrue(self.session.toggle_curtain())
        with patch("media.session.time.monotonic", return_value=17.75):
            path, position = self.session._curtain_position()

        self.assertEqual(path, Path("virtual.wav"))
        self.assertAlmostEqual(position, 0.25)
        self.assertFalse(self.session.toggle_curtain())
        self.assertIsNone(self.session._curtain_position())
        with patch("media.session.time.monotonic", return_value=30.0):
            self.assertTrue(self.session.toggle_curtain())
            self.assertEqual(self.session._curtain_position(), (Path("virtual.wav"), 0.0))

    def test_clip_loop_uses_source_fps_independent_of_30fps_output(self):
        capture = Mock()
        capture.get.side_effect = lambda prop: {
            cv2.CAP_PROP_FPS: 24,
            cv2.CAP_PROP_FRAME_COUNT: 60,
        }[prop]
        first_frame = np.zeros((2, 2, 3), dtype=np.uint8)
        second_frame = np.ones((2, 2, 3), dtype=np.uint8)
        capture.read.side_effect = [
            (True, first_frame),
            (True, second_frame),
            (True, first_frame),
        ]
        with patch("media.curtain.cv2.VideoCapture", return_value=capture):
            clip = CurtainVideo("virtual.mp4")
            try:
                self.assertEqual(clip.duration, 2.5)
                self.assertIs(clip.frame_at(1 / 30), first_frame)
                self.assertIs(clip.frame_at(2 / 30), second_frame)
                self.assertIs(clip.frame_at(2.5), first_frame)
            finally:
                clip.close()

        capture.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 0)
        capture.release.assert_called_once()

    def test_renderer_keeps_30fps_while_camera_reader_is_blocked(self):
        frame = np.full((24, 32, 3), 160, dtype=np.uint8)
        self.session._camera_frame = frame
        self.session._streamer = self.streamer
        reader_started = threading.Event()
        release_reader = threading.Event()
        self.addCleanup(release_reader.set)
        reader_threads = []
        clock = [0.0]
        waits = []
        payloads = []

        def blocked_reader():
            reader_threads.append(threading.get_ident())
            reader_started.set()
            release_reader.wait(timeout=2)

        def write_frame(payload):
            self.assertTrue(reader_started.wait(timeout=1))
            self.assertFalse(release_reader.is_set())
            payloads.append(payload)

        def wait_for_frame(delay):
            waits.append(delay)
            clock[0] += delay
            if len(waits) == 3:
                self.session._stop.set()
                release_reader.set()
            return self.session._stop.is_set()

        self.streamer.write.side_effect = write_frame
        with (
            patch.object(self.session, "_read_camera_windows", side_effect=blocked_reader),
            patch("media.session.time.monotonic", side_effect=lambda: clock[0]),
            patch.object(self.session._stop, "wait", side_effect=wait_for_frame),
        ):
            self.session._capture_windows()

        self.assertNotEqual(reader_threads, [threading.get_ident()])
        self.assertEqual(len(payloads), 3)
        for delay in waits:
            self.assertAlmostEqual(delay, 1 / 30)
        for payload in payloads:
            decoded = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_allclose(decoded, frame, atol=2)
        self.assertIs(self.session.live_frames.get_nowait(), frame)
        self.assertIs(self.session.preview_frames.get_nowait()[0], frame)
        self.assertTrue(self.session.errors.empty())
        self.streamer.stop_streaming.assert_called_once()

    def test_renderer_curtain_uses_elapsed_scene_clock_and_publishes_output(self):
        camera_frame = np.zeros((24, 32, 3), dtype=np.uint8)
        curtain_frame = np.full((24, 32, 3), 224, dtype=np.uint8)
        self.session._camera_frame = camera_frame
        self.session._curtain_active = True
        self.session._scene_started_at = 10.0
        self.session._streamer = self.streamer
        self.clip.frame_at.return_value = curtain_frame
        self.streamer.write.side_effect = lambda _: self.session._stop.set()

        with (
            patch("media.session.threading.Thread") as camera_thread,
            patch("media.session.time.monotonic", return_value=17.75),
        ):
            self.session._capture_windows()

        camera_thread.return_value.start.assert_called_once()
        self.clip.frame_at.assert_called_once_with(7.75)
        self.assertAlmostEqual(self.session._curtain_duration, 2.5)
        np.testing.assert_array_equal(self.session.live_frames.get_nowait(), curtain_frame)
        self.assertIs(self.session.preview_frames.get_nowait()[0], camera_frame)
        self.clip.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
