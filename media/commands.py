"""Build FFmpeg commands without starting processes or accessing devices."""

import os
import platform
import sys
from collections.abc import Iterable, Mapping

RTMP_URLS = {
    "Facebook": "rtmps://live-api-s.facebook.com:443/rtmp/{}",
    "Youtube": "rtmp://a.rtmp.youtube.com/live2/{}",
    "Twitch": "rtmp://live.twitch.tv/app/{}",
    # Retained from the original application; X requires a configured endpoint.
    "X": "rtmp://X.com/",
}


def get_ffmpeg_path(platform_name=None, bundle_path=None):
    """Resolve the bundled executable, falling back to FFmpeg on PATH."""
    platform_name = platform_name or platform.system()
    if bundle_path is None:
        bundle_path = getattr(sys, "_MEIPASS", None)
    if bundle_path is None:
        return "ffmpeg"
    executable = "ffmpeg.exe" if platform_name == "Windows" else "ffmpeg"
    return os.path.join(bundle_path, executable)


def get_audio_backend(platform_name=None):
    """Return the audio capture backend for a supported desktop platform."""
    platform_name = platform_name or platform.system()
    backends = {"Windows": "dshow", "Darwin": "avfoundation"}
    try:
        return backends[platform_name]
    except KeyError as error:
        raise ValueError(f"Unsupported audio platform: {platform_name}") from error


def environment_paths(environment: Mapping[str, str], bundle_path=None):
    """Compute the existing macOS media paths without changing the environment."""
    if bundle_path is None:
        bundle_path = getattr(sys, "_MEIPASS", None)
    if bundle_path is not None:
        library_path = (
            f"{bundle_path}/lib:{bundle_path}/gstreamer-1.0:"
            f"{environment.get('DYLD_LIBRARY_PATH', '')}"
        )
        plugin_path = f"{bundle_path}/gstreamer-1.0"
    else:
        library_path = "/usr/local/lib:/opt/homebrew/lib"
        plugin_path = "/usr/local/lib/gstreamer-1.0"
    path_entries = environment.get("PATH", "").split(os.pathsep)
    if "/opt/homebrew/bin" not in path_entries:
        path_entries.append("/opt/homebrew/bin")
    return {
        "DYLD_LIBRARY_PATH": library_path,
        "GST_PLUGIN_PATH": plugin_path,
        "OPENCV_VIDEOIO_PRIORITY_LIST": "gst",
        "PATH": os.pathsep.join(path_entries),
    }


def set_environment_paths():
    """Configure media discovery explicitly when the application starts on macOS."""
    if platform.system() == "Darwin":
        os.environ.update(environment_paths(os.environ))


def build_stream_targets(destinations: Iterable[tuple[str, str]], custom_server: str = "") -> str:
    """Build the FFmpeg tee destination list in the caller's selected order."""
    streams = []
    for platform_name, stream_key in destinations:
        if platform_name == "Otro":
            url = custom_server + stream_key
        else:
            try:
                url = RTMP_URLS[platform_name].format(stream_key)
            except KeyError as error:
                raise ValueError(f"Unknown streaming platform: {platform_name}") from error
        streams.append(f"[f=flv:onfail=ignore]{url}")
    return "|".join(streams)


def build_camera_stream_command(audio_device, streams, *, platform_name=None, ffmpeg_path=None):
    """Encode JPEG frames from stdin and audio from the selected input device."""
    return [
        ffmpeg_path or get_ffmpeg_path(platform_name),
        "-y",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-framerate",
        "30",
        "-i",
        "-",
        "-f",
        get_audio_backend(platform_name),
        "-thread_queue_size",
        "16384",
        "-rtbufsize",
        "100M",
        "-i",
        f"audio={audio_device}",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-b:v",
        "3500k",
        "-maxrate",
        "6000k",
        "-bufsize",
        "12000k",
        "-b:a",
        "96k",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "60",
        "-vsync",
        "cfr",
        "-async",
        "1",
        "-use_wallclock_as_timestamps",
        "1",
        "-copyts",
        "-start_at_zero",
        "-vf",
        "setpts=PTS+2/TB",
        "-f",
        "tee",
        streams,
    ]


def build_curtain_stream_command(
    video_pipe, audio_pipe, width, height, channels, rate, streams, *, ffmpeg_path=None
):
    """Encode JPEG video and PCM audio; use ``-`` for video supplied on stdin."""
    return [
        ffmpeg_path or get_ffmpeg_path(),
        "-y",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-pixel_format",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-thread_queue_size",
        "4096",
        "-framerate",
        "30",
        "-i",
        video_pipe,
        "-f",
        "s16le",
        "-ac",
        str(channels),
        "-ar",
        str(rate),
        "-thread_queue_size",
        "4096",
        "-i",
        audio_pipe,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-b:v",
        "6800k",
        "-maxrate",
        "6800k",
        "-bufsize",
        "13600k",
        "-b:a",
        "128k",
        "-pix_fmt",
        "yuv420p",
        "-vsync",
        "cfr",
        "-async",
        "1",
        "-use_wallclock_as_timestamps",
        "1",
        "-copyts",
        "-g",
        "60",
        "-flags",
        "+global_header",
        "-f",
        "tee",
        streams,
    ]


def build_extract_audio_command(video_path, audio_path, *, ffmpeg_path=None):
    """Extract a media file's audio using the application's original settings."""
    return [
        ffmpeg_path or get_ffmpeg_path(),
        "-y",
        "-i",
        video_path,
        "-q:a",
        "0",
        "-map",
        "a",
        audio_path,
    ]
