"""Command and device parsing regressions; no hardware or FFmpeg is invoked."""

import os
import unittest
from unittest.mock import Mock, patch

from media.commands import (
    build_camera_stream_command,
    build_curtain_stream_command,
    build_extract_audio_command,
    build_stream_targets,
    environment_paths,
    get_audio_backend,
    get_ffmpeg_path,
    set_environment_paths,
)
from media.devices import list_devices, parse_device_list


def values_for_option(command, option):
    return [command[index + 1] for index, value in enumerate(command[:-1]) if value == option]


class MediaCommandsTests(unittest.TestCase):
    def test_bundled_executable_matches_platform(self):
        for platform_name, executable in (
            ("Windows", "ffmpeg.exe"),
            ("Darwin", "ffmpeg"),
        ):
            with self.subTest(platform_name=platform_name):
                self.assertEqual(
                    get_ffmpeg_path(platform_name, "bundle"),
                    os.path.join("bundle", executable),
                )

    def test_backend_rejects_unsupported_platform(self):
        self.assertEqual(get_audio_backend("Windows"), "dshow")
        self.assertEqual(get_audio_backend("Darwin"), "avfoundation")
        with self.assertRaisesRegex(ValueError, "Linux"):
            get_audio_backend("Linux")

    def test_environment_configuration_is_explicit_and_macos_only(self):
        initial = {"PATH": "original-path"}
        with patch.dict(os.environ, initial, clear=True):
            with patch("media.commands.platform.system", return_value="Windows"):
                set_environment_paths()
            self.assertEqual(dict(os.environ), initial)
        result = environment_paths(initial, "bundle")
        self.assertEqual(initial, {"PATH": "original-path"})
        self.assertEqual(result["GST_PLUGIN_PATH"], "bundle/gstreamer-1.0")
        repeated = environment_paths(result, "bundle")
        self.assertEqual(repeated["PATH"], result["PATH"])

    def test_multiple_targets_keep_selection_order_without_empty_target(self):
        targets = build_stream_targets(
            [
                ("Youtube", "youtube-test"),
                ("Facebook", "facebook-test"),
                ("Otro", "custom-test"),
            ],
            custom_server="rtmp://example.invalid/live/",
        )
        self.assertEqual(
            targets.split("|"),
            [
                "[f=flv:onfail=ignore]rtmp://a.rtmp.youtube.com/live2/youtube-test",
                "[f=flv:onfail=ignore]rtmps://live-api-s.facebook.com:443/rtmp/facebook-test",
                "[f=flv:onfail=ignore]rtmp://example.invalid/live/custom-test",
            ],
        )
        self.assertEqual(build_stream_targets([]), "")
        with self.assertRaisesRegex(ValueError, "Unknown streaming platform"):
            build_stream_targets([("Unknown", "test")])

    def test_camera_command_keeps_device_name_as_a_single_argument(self):
        device = 'Microphone with spaces "and quotes"'
        targets = "rtmp://example.invalid/live/test"
        command = build_camera_stream_command(
            device, targets, platform_name="Windows", ffmpeg_path="custom ffmpeg.exe"
        )
        self.assertEqual(command[0], "custom ffmpeg.exe")
        self.assertEqual(values_for_option(command, "-i"), ["-", f"audio={device}"])
        self.assertEqual(values_for_option(command, "-framerate"), ["30"])
        self.assertLess(command.index("-framerate"), command.index("-i"))
        self.assertEqual(values_for_option(command, "-map"), ["0:v", "1:a"])
        self.assertEqual(values_for_option(command, "-b:v"), ["3500k"])
        self.assertEqual(values_for_option(command, "-b:a"), ["96k"])
        self.assertEqual(command[-2:], ["tee", targets])
        self.assertTrue(all(isinstance(argument, str) for argument in command))

    def test_curtain_command_accepts_stdin_and_configurable_pcm_audio(self):
        command = build_curtain_stream_command(
            "-",
            "audio pipe",
            1280,
            720,
            1,
            44100,
            "destination",
            ffmpeg_path="ffmpeg",
            platform_name="Windows",
        )
        self.assertEqual(values_for_option(command, "-i"), ["-", "audio pipe"])
        self.assertEqual(values_for_option(command, "-framerate"), ["30"])
        self.assertLess(command.index("-framerate"), command.index("-i"))
        self.assertIn("scale=1280:720:", values_for_option(command, "-vf")[0])
        self.assertEqual(values_for_option(command, "-ac"), ["1"])
        self.assertEqual(values_for_option(command, "-ar"), ["44100"])
        self.assertEqual(values_for_option(command, "-b:v"), ["6800k"])
        self.assertEqual(values_for_option(command, "-b:a"), ["128k"])
        self.assertEqual(command[-1], "destination")

    def test_extract_audio_preserves_paths_and_selects_audio(self):
        command = build_extract_audio_command(
            "video file.mp4",
            "audio file.wav",
            ffmpeg_path="ffmpeg",
            platform_name="Windows",
            channels=2,
            rate=44100,
        )
        self.assertEqual(values_for_option(command, "-i"), ["video file.mp4"])
        self.assertEqual(values_for_option(command, "-map"), ["0:a:0"])
        self.assertEqual(values_for_option(command, "-c:a"), ["pcm_s16le"])
        self.assertEqual(values_for_option(command, "-ac"), ["2"])
        self.assertEqual(values_for_option(command, "-ar"), ["44100"])
        self.assertEqual(values_for_option(command, "-f"), ["wav"])
        self.assertIn("-nostdin", command)
        self.assertEqual(command[-1], "audio file.wav")

    def test_windows_stream_commands_use_current_ffmpeg_timing_options(self):
        commands = [
            build_camera_stream_command("Mic", "destination", platform_name="Windows"),
            build_curtain_stream_command(
                "-",
                r"\\.\pipe\audio",
                1280,
                720,
                2,
                44100,
                "destination",
                platform_name="Windows",
            ),
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(values_for_option(command, "-fps_mode"), ["cfr"])
                self.assertEqual(values_for_option(command, "-flags"), ["+global_header"])
                # Each input gets a small probe budget, before its own -i.
                input_start = 1
                for index, value in enumerate(command):
                    if value == "-i":
                        input_options = command[input_start:index]
                        self.assertEqual(values_for_option(input_options, "-probesize"), ["32"])
                        self.assertEqual(
                            values_for_option(input_options, "-analyzeduration"), ["0"]
                        )
                        self.assertEqual(values_for_option(input_options, "-fpsprobesize"), ["0"])
                        input_start = index + 2
                for option in ("-vsync", "-async", "-copyts", "-use_wallclock_as_timestamps"):
                    self.assertNotIn(option, command)

    def test_windows_curtains_fix_output_canvas_to_even_dimensions(self):
        command = build_curtain_stream_command(
            "-", "audio", 321, 181, 2, 44100, "destination", platform_name="Windows"
        )
        video_filter = values_for_option(command, "-vf")[0]
        self.assertIn("scale=322:182:", video_filter)
        self.assertIn("pad=322:182:", video_filter)
        self.assertIn("force_divisible_by=2", video_filter)
        self.assertNotIn("-pixel_format", command)
        with self.assertRaises(ValueError):
            build_curtain_stream_command(
                "-", "audio", 0, 720, 2, 44100, "destination", platform_name="Windows"
            )

    def test_macos_commands_keep_existing_options(self):
        camera = build_camera_stream_command("Mic", "destination", platform_name="Darwin")
        curtain = build_curtain_stream_command(
            "-", "audio", 1280, 720, 2, 44100, "destination", platform_name="Darwin"
        )
        for command in (camera, curtain):
            self.assertEqual(values_for_option(command, "-vsync"), ["cfr"])
            self.assertIn("-copyts", command)
            self.assertNotIn("-probesize", command)
        self.assertEqual(values_for_option(camera, "-vf"), ["setpts=PTS+2/TB"])
        self.assertEqual(values_for_option(curtain, "-s"), ["1280x720"])
        extraction = build_extract_audio_command("clip.mp4", "audio.wav", platform_name="Darwin")
        self.assertEqual(values_for_option(extraction, "-map"), ["a"])
        self.assertEqual(values_for_option(extraction, "-q:a"), ["0"])


class DeviceListTests(unittest.TestCase):
    def test_windows_devices_preserve_order_and_ignore_alternative_names(self):
        output = """[dshow @ 0] "Camera A" (video)
[dshow @ 0] Alternative name "@device:camera-a"
[dshow @ 0] "Microphone A" (audio)
[dshow @ 0] "Camera A" (video)
[dshow @ 0] "Camera B" (video)
[dshow @ 0] "Unknown" (other)"""
        self.assertEqual(
            parse_device_list(output, "Windows"),
            (["Camera A", "Camera B"], ["Microphone A"]),
        )

    def test_macos_devices_exclude_screen_capture_and_track_sections(self):
        output = """[AVFoundation @ 0] AVFoundation video devices:
[AVFoundation @ 0] [0] Camera A
[AVFoundation @ 0] [1] Capture screen 0
[AVFoundation @ 0] AVFoundation audio devices:
[AVFoundation @ 0] [0] Microphone A
[AVFoundation @ 0] [1] Microphone A
dummy: Input/output error"""
        self.assertEqual(parse_device_list(output, "Darwin"), (["Camera A"], ["Microphone A"]))

    @patch("media.devices.subprocess.run")
    def test_discovery_reads_stderr_despite_nonzero_ffmpeg_exit(self, run):
        run.return_value = Mock(returncode=1, stderr='[dshow @ 0] "Camera" (video)')
        with patch("media.devices.subprocess.STARTUPINFO", create=True) as startup_info:
            startup_info.return_value.dwFlags = 0
            with patch("media.devices.subprocess.STARTF_USESHOWWINDOW", 1, create=True):
                self.assertEqual(list_devices("Windows"), (["Camera"], []))
        command = run.call_args.args[0]
        self.assertIn("dshow", command)
        self.assertFalse(run.call_args.kwargs["check"])
        run.assert_called_once()

    @patch("media.devices.subprocess.run")
    def test_unsupported_platform_does_not_launch_ffmpeg(self, run):
        self.assertEqual(list_devices("Linux"), ([], []))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
