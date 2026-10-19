"""Exercise the complete Windows media path with synthetic devices and local FLV."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from media.session import StreamingSession, StreamSettings


class SyntheticMicrophone:
    """A deterministic 220 Hz microphone, without opening any capture hardware."""

    def __init__(self):
        self.samples = 0

    def get_read_available(self):
        return 1024

    def read(self, count, **kwargs):
        positions = np.arange(self.samples, self.samples + count)
        self.samples += count
        samples = (6000 * np.sin(positions * 2 * np.pi * 220 / 44100)).astype("<i2")
        return np.repeat(samples, 2).tobytes()

    def stop_stream(self):
        pass

    def close(self):
        pass


@unittest.skipUnless(
    sys.platform == "win32" and shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "Requires Windows and FFmpeg tools",
)
class WindowsStreamingTests(unittest.TestCase):
    def run_command(self, command):
        result = subprocess.run(command, capture_output=True, timeout=20)
        self.assertEqual(
            result.returncode,
            0,
            result.stderr.decode(errors="replace")
            + "\n"
            + "\n".join(getattr(self, "diagnostics", [])),
        )
        return result.stdout

    def test_switch_camera_curtain_camera_keeps_one_process_and_both_audio_sources(self):
        self.exercise_scene_switch(initial_curtain=False)

    def test_curtain_start_matches_misdecoded_microphone_and_keeps_audio_synchronized(self):
        self.exercise_scene_switch(initial_curtain=True)

    def exercise_scene_switch(self, *, initial_curtain):
        with tempfile.TemporaryDirectory(prefix="streaming-session-test-") as temporary:
            directory = Path(temporary)
            curtain = directory / "curtain.mp4"
            output = directory / "output.flv"
            self.run_command(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=160x90:r=24:d=1.2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:sample_rate=48000:duration=1.2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(curtain),
                ]
            )
            audio = Mock()
            device_name = "Micrófono de prueba"
            reported_name = (
                device_name.encode("utf-8").decode("cp1252") if initial_curtain else device_name
            )
            audio.get_device_count.return_value = 1
            audio.get_device_info_by_index.return_value = {
                "name": reported_name,
                "maxInputChannels": 2,
            }
            audio.open.return_value = SyntheticMicrophone()
            session = StreamingSession()
            diagnostics = []
            self.diagnostics = diagnostics
            report_diagnostic = session._handle_ffmpeg_output

            def camera_reader():
                session._camera_frame = np.full((90, 160, 3), 64, dtype=np.uint8)
                session._stop.wait(15)

            def diagnostic(line):
                diagnostics.append(line)
                report_diagnostic(line)

            with (
                patch("pyaudio.PyAudio", return_value=audio),
                patch.object(session, "_read_camera_windows", side_effect=camera_reader),
                patch(
                    "media.session.build_stream_targets",
                    # A local file must flush even if the live process is terminated.
                    return_value=f"[f=flv:flush_packets=1]{output.as_posix()}",
                ),
            ):
                try:
                    session.select_camera("0")
                    session.select_curtain(str(curtain))
                    if initial_curtain:
                        session.toggle_curtain()
                    session.start_preview()
                    session.preview_frames.get(timeout=2)
                    with patch.object(session, "_handle_ffmpeg_output", side_effect=diagnostic):
                        session.start_stream(
                            StreamSettings(
                                device_name,
                                (("Otro", "unused"),),
                                use_curtains=True,
                            )
                        )
                    streamer = session._streamer
                    time.sleep(1.5)
                    self.assertTrue(session.is_streaming, "\n".join(diagnostics))
                    self.assertEqual(session.toggle_curtain(), not initial_curtain)
                    time.sleep(2.5)  # Cross the 1.2-second clip's loop boundary twice.
                    self.assertEqual(session.toggle_curtain(), initial_curtain)
                    time.sleep(1)
                    self.assertIs(session._streamer, streamer, "\n".join(diagnostics))
                    self.assertTrue(session.is_streaming, "\n".join(diagnostics))
                    self.assertTrue(session.errors.empty(), "\n".join(diagnostics))
                    worker = session._audio._thread
                finally:
                    session.close()
            self.assertFalse(worker.is_alive())
            self.assertFalse(session._thread.is_alive())
            self.assertTrue(output.exists(), "\n".join(diagnostics))
            probe = json.loads(
                self.run_command(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_streams",
                        "-show_packets",
                        "-of",
                        "json",
                        str(output),
                    ]
                )
            )
            streams = {stream["codec_type"]: stream for stream in probe["streams"]}
            self.assertEqual(streams["audio"]["sample_rate"], "44100")
            self.assertEqual(streams["audio"]["channels"], 2)
            self.assertEqual(streams["video"]["r_frame_rate"], "30/1")
            endings = [
                max(
                    float(packet["pts_time"])
                    for packet in probe["packets"]
                    if packet["codec_type"] == kind
                )
                for kind in ("video", "audio")
            ]
            self.assertLess(abs(endings[0] - endings[1]), 0.15, endings)
            pcm = self.run_command(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(output),
                    "-map",
                    "0:a",
                    "-f",
                    "s16le",
                    "-ac",
                    "1",
                    "pipe:1",
                ]
            )
            samples = np.frombuffer(pcm, dtype="<i2").astype(float)
            frequencies = []
            size = 4410
            for offset in range(0, len(samples) - size, size):
                block = samples[offset : offset + size]
                spectrum = abs(np.fft.rfft(block * np.hanning(size)))
                frequencies.append(np.argmax(spectrum) * 44100 / size)
            self.assertTrue(any(abs(value - 220) < 20 for value in frequencies), frequencies)
            self.assertTrue(any(abs(value - 880) < 20 for value in frequencies), frequencies)
            capture = cv2.VideoCapture(str(output))
            colors = []
            try:
                while True:
                    success, frame = capture.read()
                    if not success:
                        break
                    colors.append(frame.mean(axis=(0, 1)))
            finally:
                capture.release()
            self.assertTrue(any(red > 200 and blue < 20 for blue, _, red in colors))
            self.assertTrue(
                any(abs(red - 64) < 5 and abs(blue - 64) < 5 for blue, _, red in colors)
            )
            red_frames = [
                index for index, (blue, _, red) in enumerate(colors) if red > 200 and blue < 20
            ]
            curtain_blocks = [
                index for index, frequency in enumerate(frequencies) if abs(frequency - 880) < 20
            ]
            video_start = float(streams["video"].get("start_time", 0))
            audio_start = float(streams["audio"].get("start_time", 0))
            for frame_index, block_index in (
                (red_frames[0], curtain_blocks[0]),
                (red_frames[-1], curtain_blocks[-1]),
            ):
                self.assertAlmostEqual(
                    video_start + frame_index / 30, audio_start + block_index / 10, delta=0.15
                )


if __name__ == "__main__":
    unittest.main()
