"""Session lifecycle tests without cameras, audio devices or subprocesses."""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from media.session import StreamingSession, StreamSettings


class StreamingSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = StreamingSession()
        self.addCleanup(self.session.close)
        self.session.select_camera("0")
        self.settings = StreamSettings("Microphone", (("Youtube", "test-key"),))
        self.streamer_class = self.enterContext(patch("media.session.Streamer"))
        self.streamer = self.streamer_class.return_value
        self.streamer.is_streaming.return_value = True
        self.enterContext(patch("media.session.platform.system", return_value="Darwin"))

    def test_start_stop_and_restart_are_idempotent(self):
        self.session.start_stream(self.settings)
        self.session.start_stream(self.settings)
        self.assertEqual(self.streamer_class.call_count, 1)
        self.assertTrue(self.session.is_streaming)

        self.session.stop_stream()
        self.session.stop_stream()
        self.assertFalse(self.session.is_streaming)
        self.streamer.stop_streaming.assert_called_once()

        self.session.start_stream(self.settings)
        self.assertEqual(self.streamer_class.call_count, 2)
        self.assertTrue(self.session.is_streaming)

    def test_preview_thread_can_be_closed_and_restarted(self):
        def capture_until_closed():
            self.session._stop.wait(2)

        with patch.object(self.session, "_capture", side_effect=capture_until_closed):
            self.session.start_preview()
            first_thread = self.session._thread
            self.session.start_preview()
            self.assertIs(self.session._thread, first_thread)
            self.session.close()
            self.assertFalse(first_thread.is_alive())

            self.session.start_preview()
            self.assertIsNot(self.session._thread, first_thread)
            self.session.close()
            self.assertFalse(self.session._thread.is_alive())

    def test_preview_buffer_keeps_only_latest_frame(self):
        old_frame, latest_frame = object(), object()
        self.session._publish_preview(old_frame)
        self.session._publish_preview(latest_frame)

        self.assertEqual(self.session.preview_frames.qsize(), 1)
        frame, curtain_active = self.session.preview_frames.get_nowait()
        self.assertIs(frame, latest_frame)
        self.assertFalse(curtain_active)

    def test_ffmpeg_errors_are_reported_once_and_never_expose_stream_keys(self):
        for _ in range(20):
            self.session._handle_ffmpeg_output("Failed: rtmp://server/private-stream-key")

        self.assertEqual(self.session.errors.qsize(), 1)
        while not self.session.errors.empty():
            self.assertNotIn("private-stream-key", self.session.errors.get_nowait())

    def test_error_queue_stays_bounded(self):
        for _ in range(20):
            self.session._report_error("Capture failed")
        self.assertEqual(self.session.errors.qsize(), 10)

    def test_audio_cause_is_not_overwritten_by_ffmpeg_and_shutdown_errors(self):
        self.session.start_stream(self.settings)
        self.session._handle_audio_error("No se encontró el micrófono seleccionado.")
        self.session._handle_ffmpeg_output("Error opening input: private-stream-key")
        self.session._stop_failed_stream(self.streamer, "Revise la conexión y el audio.")

        self.assertFalse(self.session.is_streaming)
        self.assertTrue(self.session._audio_failed.is_set())
        self.assertEqual(self.session.errors.qsize(), 1)
        self.assertEqual(
            self.session.errors.get_nowait(), "No se encontró el micrófono seleccionado."
        )

        self.session.start_stream(self.settings)
        self.session._handle_audio_error("El micrófono se desconectó.")
        self.assertEqual(self.session.errors.get_nowait(), "El micrófono se desconectó.")

    def test_validation_identifies_the_missing_device_before_startup(self):
        self.session.select_camera(None)
        with self.assertRaisesRegex(ValueError, "dispositivo de video"):
            self.session.start_stream(self.settings)
        self.session.select_camera("0")
        with self.assertRaisesRegex(ValueError, "dispositivo de audio"):
            self.session.start_stream(StreamSettings("", self.settings.destinations))
        self.streamer_class.assert_not_called()

    def test_curtain_audio_format_matches_command_and_resources_are_removed(self):
        self.session.select_curtain("clip.mp4")
        settings = StreamSettings("Microphone", self.settings.destinations, use_curtains=True)
        with (
            patch("media.session.subprocess.run") as extract,
            patch("media.session.AudioSource.start") as start_audio,
            patch("media.session.AudioSource.stop") as stop_audio,
        ):
            self.session.start_stream(settings)
            directory = Path(self.session._temporary.name)
            audio = self.session._audio
            command = extract.call_args.args[0]
            self.assertEqual(command[-5:-1], ["-ac", "2", "-ar", "44100"])
            self.assertEqual(extract.call_args.kwargs["timeout"], 60)
            stream_command = self.streamer_class.call_args.args[3]
            self.assertIn(str(audio.pipe_path), stream_command)
            self.assertIn("-", stream_command)
            self.assertIsNone(audio.curtain_audio())
            self.assertTrue(self.session.toggle_curtain())
            self.assertEqual(audio.curtain_audio(), directory / "curtain.wav")
            start_audio.assert_called_once()

            self.session.stop_stream()
            stop_audio.assert_called_once()
            self.assertFalse(directory.exists())
            self.assertIsNone(self.session._audio)
            self.assertIsNone(self.session._curtain_audio)

    def test_failed_curtain_extraction_removes_temporary_directory(self):
        self.session.select_curtain("clip.mp4")
        settings = StreamSettings("Microphone", self.settings.destinations, use_curtains=True)
        directories = []

        def fail_extraction(command, **kwargs):
            directories.append(Path(command[-1]).parent)
            raise subprocess.CalledProcessError(1, command)

        with (
            patch("media.session.subprocess.run", side_effect=fail_extraction),
            self.assertRaises(ValueError),
        ):
            self.session.start_stream(settings)

        self.assertFalse(self.session.is_streaming)
        self.assertFalse(directories[0].exists())
        self.assertIsNone(self.session._temporary)
        self.streamer_class.assert_not_called()

    def test_failed_streamer_start_releases_partially_started_process(self):
        self.streamer.start_streaming.side_effect = OSError("cannot start diagnostics")
        with self.assertRaises(OSError):
            self.session.start_stream(self.settings)

        self.assertFalse(self.session.is_streaming)
        self.streamer.stop_streaming.assert_called_once()

    def test_capture_failure_stops_transmission_and_releases_camera(self):
        self.session.start_stream(self.settings)
        camera = Mock()
        camera.isOpened.return_value = True
        camera.read.side_effect = OSError("camera disconnected")
        with patch.object(self.session, "_open_camera", return_value=camera):
            self.session._capture()

        self.assertFalse(self.session.is_streaming)
        self.streamer.stop_streaming.assert_called_once()
        camera.release.assert_called_once()
        self.assertFalse(self.session.errors.empty())

    def test_delayed_old_stream_failure_does_not_stop_restarted_transmission(self):
        new_streamer = Mock()
        new_streamer.is_streaming.return_value = True
        self.streamer_class.side_effect = [self.streamer, new_streamer]
        self.session.start_stream(self.settings)
        self.session.stop_stream()
        self.session.start_stream(self.settings)

        self.session._stop_failed_stream(self.streamer, "Old process failed")

        self.assertTrue(self.session.is_streaming)
        new_streamer.stop_streaming.assert_not_called()
        self.streamer.stop_streaming.assert_called_once()
        self.assertTrue(self.session.errors.empty())

        self.session._stop_failed_stream(new_streamer, "Current process failed")

        self.assertFalse(self.session.is_streaming)
        new_streamer.stop_streaming.assert_called_once()
        self.assertEqual(self.session.errors.get_nowait(), "Current process failed")

    def test_capture_encodes_jpeg_keeps_latest_preview_and_closes_resources(self):
        frames = [np.full((24, 32, 3), value, dtype=np.uint8) for value in (32, 224)]
        camera = Mock()
        camera.isOpened.return_value = True
        camera.read.side_effect = [(True, frame) for frame in frames]
        payloads = []

        def stop_after_two_frames(data):
            payloads.append(data)
            if len(payloads) == 2:
                self.session._stop.set()

        self.streamer.write.side_effect = stop_after_two_frames
        self.session.start_stream(self.settings)
        with patch.object(self.session, "_open_camera", return_value=camera):
            self.session._capture()

        self.assertEqual(len(payloads), 2)
        for payload, original in zip(payloads, frames, strict=True):
            decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.shape, original.shape)
            np.testing.assert_allclose(decoded, original, atol=2)
        self.assertEqual(self.session.preview_frames.qsize(), 1)
        preview, curtain_active = self.session.preview_frames.get_nowait()
        self.assertIs(preview, frames[-1])
        self.assertFalse(curtain_active)
        self.assertTrue(self.session.errors.empty())
        self.assertFalse(self.session.is_streaming)
        camera.release.assert_called_once()
        self.streamer.stop_streaming.assert_called_once()


if __name__ == "__main__":
    unittest.main()
