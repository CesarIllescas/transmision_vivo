"""Discover desktop capture devices through FFmpeg."""

import platform
import re
import subprocess

from media.commands import get_audio_backend, get_ffmpeg_path


def parse_device_list(output, platform_name):
    """Parse FFmpeg diagnostics, preserving device order and removing duplicates."""
    video_devices = []
    audio_devices = []
    current_devices = None
    for line in output.splitlines():
        if platform_name == "Windows":
            match = re.search(r'"(.*?)" \((.*?)\)', line)
            if not match:
                continue
            device_name, device_type = match.groups()
            if device_type == "video":
                current_devices = video_devices
            elif device_type == "audio":
                current_devices = audio_devices
            else:
                continue
        elif platform_name == "Darwin":
            if "AVFoundation video devices:" in line:
                current_devices = video_devices
                continue
            if "AVFoundation audio devices:" in line:
                current_devices = audio_devices
                continue
            match = re.search(r"\[\d+\] (.*)", line)
            if current_devices is None or not match:
                continue
            device_name = match.group(1)
            if current_devices is video_devices and "Capture screen" in device_name:
                continue
        else:
            continue
        if device_name not in current_devices:
            current_devices.append(device_name)
    return video_devices, audio_devices


def list_devices(platform_name=None):
    """Return video and audio names without opening a capture device."""
    platform_name = platform_name or platform.system()
    if platform_name not in ("Windows", "Darwin"):
        return [], []
    options = {"capture_output": True, "text": True, "encoding": "utf-8"}
    if platform_name == "Windows":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        options["startupinfo"] = startupinfo
        command = [
            get_ffmpeg_path(platform_name),
            "-f",
            get_audio_backend(platform_name),
            "-list_devices",
            "true",
            "-i",
            "dummy",
        ]
    else:
        command = [
            get_ffmpeg_path(platform_name),
            "-hide_banner",
            "-list_devices",
            "true",
            "-f",
            get_audio_backend(platform_name),
            "-i",
            "dummy",
        ]
    # FFmpeg intentionally returns a nonzero exit status after listing devices.
    result = subprocess.run(command, check=False, **options)
    return parse_device_list(result.stderr, platform_name)
