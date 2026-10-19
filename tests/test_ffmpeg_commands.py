"""Local FFmpeg integration checks using generated media, never capture devices or RTMP."""

import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from media.commands import (
    build_camera_stream_command,
    build_curtain_stream_command,
    build_extract_audio_command,
)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools unavailable")
class FFmpegCommandsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="streaming-ffmpeg-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def run_ffmpeg(self, arguments, *, data=None):
        result = subprocess.run(
            arguments, input=data, capture_output=True, cwd=self.directory, timeout=30, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        return result.stdout

    def make_sources(self):
        video = self.run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x90:rate=30",
                "-frames:v",
                "60",
                "-c:v",
                "mjpeg",
                "-f",
                "image2pipe",
                "pipe:1",
            ]
        )
        self.run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100:duration=2",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
                "audio.pcm",
            ]
        )
        return video

    def inspect_output(self, filename, width, height):
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-show_packets",
                "-of",
                "json",
                filename,
            ],
            cwd=self.directory,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        output = json.loads(result.stdout)
        streams = {stream["codec_type"]: stream for stream in output["streams"]}
        self.assertEqual(streams["video"]["codec_name"], "h264")
        self.assertEqual(streams["video"]["pix_fmt"], "yuv420p")
        self.assertEqual((streams["video"]["width"], streams["video"]["height"]), (width, height))
        self.assertEqual(streams["video"]["r_frame_rate"], "30/1")
        self.assertEqual(int(streams["video"]["nb_read_frames"]), 60)
        self.assertEqual(streams["audio"]["codec_name"], "aac")
        self.assertEqual(streams["audio"]["sample_rate"], "44100")
        self.assertEqual(streams["audio"]["channels"], 2)
        self.assertAlmostEqual(float(output["format"]["duration"]), 2, delta=0.1)
        ending_times = []
        for kind in ("video", "audio"):
            timestamps = [
                float(packet["dts_time"])
                for packet in output["packets"]
                if packet["codec_type"] == kind
            ]
            self.assertEqual(timestamps, sorted(timestamps))
            ending_times.append(timestamps[-1])
        self.assertLess(abs(ending_times[0] - ending_times[1]), 0.1)

    def test_curtain_stdin_and_pcm_produce_two_valid_flv_outputs(self):
        video = self.make_sources()
        command = build_curtain_stream_command(
            "-",
            "audio.pcm",
            321,
            181,
            2,
            44100,
            "[f=flv]first.flv|[f=flv]second.flv",
            platform_name="Windows",
        )
        self.run_ffmpeg(command, data=video)
        self.inspect_output("first.flv", 322, 182)
        self.inspect_output("second.flv", 322, 182)

    def test_camera_output_options_encode_without_the_live_audio_device(self):
        video = self.make_sources()
        command = build_camera_stream_command(
            "unused", "[f=flv]camera.flv", platform_name="Windows"
        )
        # Keep the production encoder/filter settings, replacing only the capture input.
        first_input_end = command.index("-i") + 2
        first_map = command.index("-map")
        command[first_input_end:first_map] = [
            "-f",
            "s16le",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-i",
            "audio.pcm",
        ]
        self.run_ffmpeg(command, data=video)
        self.inspect_output("camera.flv", 160, 90)

    def test_extraction_selects_first_track_and_normalizes_pcm(self):
        self.run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:sample_rate=32000:duration=1",
                "-map",
                "0:a",
                "-map",
                "1:a",
                "-c:a",
                "pcm_s16le",
                "tracks.mkv",
            ]
        )
        command = build_extract_audio_command(
            "tracks.mkv", "curtain.wav", channels=2, rate=44100, platform_name="Windows"
        )
        self.run_ffmpeg(command)
        with wave.open(str(self.directory / "curtain.wav"), "rb") as audio:
            self.assertEqual(
                (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (2, 2, 44100)
            )
            self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 1, delta=0.01)


if __name__ == "__main__":
    unittest.main()
