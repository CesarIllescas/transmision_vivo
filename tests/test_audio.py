"""Cancellable audio tests using fake PortAudio and operating system calls."""

import errno
import math
import os
import struct
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from media.audio import AUDIO_ERROR_MESSAGES, AudioSource, _microphone_candidates


class AudioSourceTests(unittest.TestCase):
    def setUp(self):
        self.audio = Mock()
        self.microphone = self.audio.open.return_value
        self.microphone.get_read_available.return_value = 1024
        self.microphone.read.return_value = bytes(4096)
        self.audio.get_device_count.return_value = 1
        self.audio.get_device_info_by_index.return_value = {
            "name": "Microphone",
            "maxInputChannels": 2,
        }
        self.pyaudio = SimpleNamespace(paInt16=8, PyAudio=Mock(return_value=self.audio))
        self.enterContext(patch.dict("sys.modules", {"pyaudio": self.pyaudio}))
        self.on_error = Mock()
        self.enterContext(patch("media.audio.platform.system", return_value="Darwin"))
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
        self.microphone.read.side_effect = OSError("private device diagnostics")
        with (
            patch.object(self.source, "_open_pipe", return_value=7),
            patch("media.audio.os.close") as close_pipe,
        ):
            self.source._run()

        self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES["read"])
        close_pipe.assert_called_once_with(7)
        self.assert_audio_closed()

    def test_missing_pyaudio_reports_error(self):
        with patch.dict("sys.modules", {"pyaudio": None}):
            self.source._run()
        self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES["module"])
        self.pyaudio.PyAudio.assert_not_called()

    def test_missing_selected_microphone_has_specific_safe_message(self):
        self.audio.get_device_count.return_value = 0
        self.source._run()
        self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES["device"])
        self.audio.open.assert_not_called()

    def test_audio_initialization_failure_has_specific_safe_message(self):
        self.pyaudio.PyAudio.side_effect = OSError("private system diagnostics")
        self.source._run()
        self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES["init"])

    def test_microphone_open_failures_distinguish_unsupported_sample_rate(self):
        for code, stage in ((-9997, "rate"), (-9999, "open")):
            with self.subTest(code=code):
                self.on_error.reset_mock()
                self.audio.open.side_effect = OSError(code, "private driver diagnostics")
                with patch.object(self.source, "_open_pipe") as open_pipe:
                    self.source._run()
                self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES[stage])
                open_pipe.assert_not_called()

    def test_pipe_connection_failure_has_specific_safe_message(self):
        with patch.object(self.source, "_open_pipe", side_effect=TimeoutError("private path")):
            self.source._run()
        self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES["pipe_connect"])
        self.assert_audio_closed()

    def test_pipe_write_failure_has_specific_safe_message(self):
        with (
            patch.object(self.source, "_open_pipe", return_value=7),
            patch.object(self.source, "_write", side_effect=BrokenPipeError("private path")),
            patch("media.audio.os.close"),
        ):
            self.source._run()
        self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES["pipe_write"])
        self.assert_audio_closed()

    def test_endpoint_creation_failure_raises_safe_message_for_caller(self):
        with (
            patch(
                "media.audio.os.mkfifo", side_effect=PermissionError("private path"), create=True
            ),
            self.assertRaises(ValueError) as failure,
        ):
            self.source.start()
        self.assertEqual(str(failure.exception), AUDIO_ERROR_MESSAGES["pipe_create"])
        self.on_error.assert_not_called()

    def test_failed_thread_start_can_be_stopped_without_masking_safe_error(self):
        self.source._windows = True
        with (
            patch("media.audio.WindowsAudioPipe") as pipe,
            patch("media.audio.threading.Thread") as thread,
        ):
            thread.return_value.start.side_effect = RuntimeError("private diagnostics")
            with self.assertRaises(ValueError) as failure:
                self.source.start()
            self.source.stop()
            pipe.return_value.close.assert_called_once()
            thread.return_value.join.assert_not_called()
        self.assertIsNone(self.source._thread)
        self.assertEqual(str(failure.exception), AUDIO_ERROR_MESSAGES["thread"])

    def test_microphone_stop_failure_still_closes_device_and_portaudio(self):
        self.microphone.read.side_effect = OSError("microphone disconnected")
        self.microphone.stop_stream.side_effect = OSError("device already stopped")
        with (
            patch.object(self.source, "_open_pipe", return_value=7),
            patch("media.audio.os.close"),
        ):
            self.source._run()
        self.assert_audio_closed()

    def make_wav(self, samples, *, channels=2, rate=44100):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "scene.wav"
        with wave.open(str(path), "wb") as clip:
            clip.setnchannels(channels)
            clip.setsampwidth(2)
            clip.setframerate(rate)
            clip.writeframes(b"".join(struct.pack("<h", sample) * channels for sample in samples))
        return path

    def run_chunks(self, count):
        payloads = []

        def record(_, data):
            payloads.append(data)
            if len(payloads) == count:
                self.source._stop.set()

        with (
            patch.object(self.source, "_open_pipe", return_value=7),
            patch.object(self.source, "_write", side_effect=record),
            patch("media.audio.os.close"),
        ):
            self.source._run()
        return payloads

    def test_windows_endpoint_exists_before_worker_starts_and_cancel_is_forwarded(self):
        with patch("media.audio.platform.system", return_value="Windows"):
            source = AudioSource(Path("unused"), "Microphone", lambda: None, self.on_error)
        self.assertIsInstance(source.pipe_path, str)
        self.assertTrue(source.pipe_path.startswith(r"\\.\pipe\streaming-"))
        order = []
        with (
            patch(
                "media.audio.WindowsAudioPipe",
                side_effect=lambda *_: order.append("pipe") or Mock(),
            ) as pipe,
            patch("media.audio.threading.Thread") as thread,
        ):
            thread.return_value.start.side_effect = lambda: order.append("worker")
            source.start()
            source.stop()
        self.assertEqual(order, ["pipe", "worker"])
        pipe.assert_called_once_with(source.pipe_path, source._stop)
        source._windows_pipe.cancel.assert_called_once()

    def test_shared_clock_seeks_and_pads_after_audio_ends_until_video_loops(self):
        path = self.make_wav([100] * 4410 + [200] * 4410)
        self.source.curtain_position = Mock(side_effect=[(path, 0.1), (path, 0.3), (path, 0.0)])
        payloads = self.run_chunks(3)

        self.assertEqual(payloads[0], struct.pack("<hh", 200, 200) * 1024)
        self.assertEqual(payloads[1], bytes(4096))
        self.assertEqual(payloads[2], struct.pack("<hh", 100, 100) * 1024)
        self.assertEqual(self.microphone.read.call_count, 3)
        self.on_error.assert_not_called()

    def test_scene_switches_keep_microphone_drained_and_resume_fresh_samples(self):
        path = self.make_wav([100] * 2048)
        self.source.curtain_position = Mock(side_effect=[(path, 0.0), None])
        self.microphone.get_read_available.side_effect = [3072, 1024]
        self.microphone.read.side_effect = [bytes(12288), struct.pack("<hh", 300, 300) * 1024]
        payloads = self.run_chunks(2)

        self.assertEqual(payloads[0], struct.pack("<hh", 100, 100) * 1024)
        self.assertEqual(payloads[1], struct.pack("<hh", 300, 300) * 1024)
        self.assertEqual(
            [call.args[0] for call in self.microphone.read.call_args_list], [3072, 1024]
        )
        self.on_error.assert_not_called()

    def test_missing_audio_reports_error_and_releases_microphone(self):
        self.source.curtain_audio = lambda: Path("missing-scene-audio.wav")
        self.assertEqual(self.run_chunks(1), [])
        self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES["clip"])
        self.assert_audio_closed()

    def test_wrong_wav_format_reports_error(self):
        path = self.make_wav([100] * 1024, rate=48000)
        self.source.curtain_audio = lambda: path
        self.assertEqual(self.run_chunks(1), [])
        self.on_error.assert_called_once()

    def test_no_microphone_samples_emits_silence_without_blocking_read(self):
        self.microphone.get_read_available.return_value = 0
        self.assertEqual(self.run_chunks(1), [bytes(4096)])
        self.microphone.read.assert_not_called()
        self.on_error.assert_not_called()

    def test_partial_microphone_frames_are_preserved_and_only_remainder_is_silent(self):
        self.microphone.get_read_available.return_value = 512
        captured = struct.pack("<hh", 321, 321) * 512
        self.microphone.read.return_value = captured

        self.assertEqual(self.run_chunks(1), [captured + bytes(512 * 2 * 2)])
        self.microphone.read.assert_called_once_with(512, exception_on_overflow=False)
        self.on_error.assert_not_called()

    def test_moderate_microphone_surplus_is_retained_for_next_block(self):
        self.microphone.get_read_available.side_effect = [1536, 512]
        first = struct.pack("<hh", 100, 100) * 1024
        remaining = struct.pack("<hh", 200, 200) * 512
        self.microphone.read.side_effect = [first, remaining]

        self.assertEqual(self.run_chunks(2), [first, remaining + bytes(512 * 2 * 2)])
        self.assertEqual(
            [call.args[0] for call in self.microphone.read.call_args_list], [1024, 512]
        )
        self.on_error.assert_not_called()

    def test_absolute_deadline_compensates_windows_timer_oversleep(self):
        clock = [0.0]
        waits = []
        timer_quantum = 1 / 64
        blocks = 100

        def rounded_wait(delay):
            waits.append(delay)
            if self.source._stop.is_set():
                return True
            clock[0] += math.ceil(delay / timer_quantum) * timer_quantum
            return False

        with (
            patch("media.audio.time.monotonic", side_effect=lambda: clock[0]),
            patch.object(self.source._stop, "wait", side_effect=rounded_wait),
        ):
            payloads = self.run_chunks(blocks)

        ideal_last_block_time = (blocks - 1) * self.source.chunk / self.source.rate
        self.assertEqual(sum(map(len, payloads)), blocks * 1024 * 2 * 2)
        self.assertGreaterEqual(clock[0], ideal_last_block_time)
        self.assertLess(clock[0] - ideal_last_block_time, timer_quantum)
        self.assertLess(waits[1], waits[0])
        self.on_error.assert_not_called()

    def test_delayed_pipe_open_pads_exact_silence_and_preserves_shared_clock(self):
        clock = [10.160]
        waits = []
        self.source.stream_started_at = lambda: 10.0
        live_audio = struct.pack("<hh", 321, 321) * 1024
        self.microphone.read.return_value = live_audio

        def advance_clock(delay):
            waits.append(delay)
            if not self.source._stop.is_set():
                clock[0] += delay
            return self.source._stop.is_set()

        with (
            patch("media.audio.time.monotonic", side_effect=lambda: clock[0]),
            patch.object(self.source._stop, "wait", side_effect=advance_clock),
        ):
            # 160 ms = 7056 frames = six full padding blocks plus 912 frames.
            payloads = self.run_chunks(9)

        self.assertEqual([len(data) for data in payloads[:7]], [4096] * 6 + [3648])
        self.assertEqual(b"".join(payloads[:7]), bytes(7056 * 2 * 2))
        self.assertEqual(payloads[7:], [live_audio, live_audio])
        self.assertAlmostEqual(waits[0], 1024 / 44100)
        self.assertAlmostEqual(clock[0], 10.160 + 1024 / 44100)
        self.on_error.assert_not_called()

    def test_mono_microphone_is_duplicated_to_stereo(self):
        self.audio.get_device_info_by_index.return_value["maxInputChannels"] = 1
        self.microphone.read.return_value = struct.pack("<h", 321) * 1024
        self.assertEqual(self.run_chunks(1), [struct.pack("<hh", 321, 321) * 1024])

    def test_wasapi_full_name_is_preferred_over_mme_and_directsound(self):
        name = "Microphone (A sufficiently long USB device name)"
        devices = [
            {"name": name[:31], "hostApi": 2, "maxInputChannels": 2},
            {"name": name, "hostApi": 1, "maxInputChannels": 2},
            {"name": name, "hostApi": 13, "maxInputChannels": 2},
        ]
        self.audio.get_device_count.return_value = len(devices)
        self.audio.get_device_info_by_index.side_effect = devices.__getitem__
        self.audio.get_host_api_info_by_index.side_effect = lambda host: {"type": host}
        self.assertEqual(_microphone_candidates(self.audio, name), [(2, 2), (1, 2)])

    def test_ambiguous_truncated_mme_names_are_rejected(self):
        name = "Microphone (A sufficiently long USB device name)"
        self.audio.get_device_count.return_value = 2
        self.audio.get_device_info_by_index.return_value = {
            "name": name[:31],
            "hostApi": 2,
            "maxInputChannels": 2,
        }
        self.audio.get_host_api_info_by_index.return_value = {"type": 2}
        self.assertEqual(_microphone_candidates(self.audio, name), [])

    def test_unique_truncated_mme_name_can_match_full_directshow_name(self):
        name = "Microphone (A sufficiently long USB device name)"
        self.audio.get_device_info_by_index.return_value = {
            "name": name[:31],
            "hostApi": 2,
            "maxInputChannels": 1,
        }
        self.audio.get_host_api_info_by_index.return_value = {"type": 2}
        self.assertEqual(_microphone_candidates(self.audio, name), [(0, 1)])

    def configure_devices(self, names_and_hosts):
        devices = [
            {"name": name, "hostApi": host, "maxInputChannels": 2} for name, host in names_and_hosts
        ]
        self.audio.get_device_count.return_value = len(devices)
        self.audio.get_device_info_by_index.side_effect = devices.__getitem__
        self.audio.get_host_api_info_by_index.side_effect = lambda host: {"type": host}

    def test_repairs_accented_windows_name_across_hosts(self):
        name = "Micrófono (Realtek(R) Audio)"
        mojibake = name.encode("utf-8").decode("cp1252")
        self.configure_devices([(mojibake, 2), (mojibake, 1), (mojibake, 13)])
        self.assertEqual(_microphone_candidates(self.audio, name), [(2, 2), (1, 2), (0, 2)])

    def test_original_exact_name_has_priority_over_repaired_name(self):
        name = "Micrófono"
        mojibake = name.encode("utf-8").decode("cp1252")
        self.configure_devices([(name, 2), (mojibake, 13)])
        self.assertEqual(_microphone_candidates(self.audio, name), [(0, 2)])
        # A literal name containing these characters must also remain selectable.
        self.assertEqual(_microphone_candidates(self.audio, mojibake), [(1, 2)])

    def test_unicode_names_are_preserved_and_canonical_accents_match(self):
        name = "Micrófono 日本 🎤"
        self.configure_devices([(name, 13)])
        self.assertEqual(_microphone_candidates(self.audio, name), [(0, 2)])
        self.assertEqual(_microphone_candidates(self.audio, "Micro\u0301fono 日本 🎤"), [(0, 2)])

    def test_raw_utf8_bytes_and_latin1_mojibake_names_are_supported(self):
        name = "Micrófono 🎤"
        for encoded in (name.encode("utf-8"), name.encode("utf-8").decode("latin1")):
            with self.subTest(name=encoded):
                self.configure_devices([(encoded, 13)])
                self.assertEqual(_microphone_candidates(self.audio, name), [(0, 2)])

    def test_distinct_names_colliding_after_encoding_repair_are_rejected(self):
        name = "Microphone 🎤"
        self.configure_devices(
            [
                (name.encode("utf-8").decode("cp1252"), 13),
                (name.encode("utf-8").decode("latin1"), 1),
            ]
        )
        self.assertEqual(_microphone_candidates(self.audio, name), [])

    def test_duplicate_repaired_names_within_one_host_are_rejected(self):
        name = "Micrófono"
        mojibake = name.encode("utf-8").decode("cp1252")
        self.configure_devices([(mojibake, 13), (mojibake, 13)])
        self.assertEqual(_microphone_candidates(self.audio, name), [])

    def test_unsupported_host_falls_back_without_reporting_a_failed_transmission(self):
        self.configure_devices([("Microphone", 13), ("Microphone", 1)])
        self.audio.open.side_effect = [OSError(-9997, "Invalid sample rate"), self.microphone]
        self.assertEqual(self.run_chunks(1), [bytes(4096)])
        self.assertEqual(
            [call.kwargs["input_device_index"] for call in self.audio.open.call_args_list], [0, 1]
        )
        self.on_error.assert_not_called()

    def test_mixed_host_open_failures_do_not_blame_only_sample_rate(self):
        self.configure_devices([("Microphone", 13), ("Microphone", 1)])
        self.audio.open.side_effect = [
            OSError(-9997, "Invalid sample rate"),
            OSError(-9999, "private permission diagnostics"),
        ]
        self.source._run()
        self.on_error.assert_called_once_with(AUDIO_ERROR_MESSAGES["open"])

    def test_legacy_scene_callback_still_loops_audio(self):
        path = self.make_wav([100] * 1024)
        self.source.curtain_audio = lambda: path
        self.assertEqual(self.run_chunks(2), [struct.pack("<hh", 100, 100) * 1024] * 2)
        self.on_error.assert_not_called()

    def test_empty_wav_reports_error_and_releases_audio(self):
        path = self.make_wav([])
        self.source.curtain_audio = lambda: path
        self.assertEqual(self.run_chunks(1), [])
        self.on_error.assert_called_once()
        self.assert_audio_closed()


if __name__ == "__main__":
    unittest.main()
