"""Media session independent of Flet controls and database access."""

from __future__ import annotations

import platform
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from classes.streamer import Streamer
from media.audio import AudioSource
from media.commands import (
    build_camera_stream_command,
    build_curtain_stream_command,
    build_extract_audio_command,
    build_stream_targets,
)
from media.frames import adaptive_resize

BLACKMAGIC_DEVICE = "blackmagic"
DECKLINK_PIPELINE = "decklinkvideosrc mode=auto connection=1 ! videoconvert ! appsink"


@dataclass(frozen=True)
class StreamSettings:
    audio_device: str
    destinations: tuple[tuple[str, str], ...]
    custom_server: str = ""
    use_curtains: bool = False


class StreamingSession:
    """Own capture, preview buffers, FFmpeg and optional curtain audio for one view."""

    def __init__(self) -> None:
        self.preview_frames: queue.Queue[tuple[np.ndarray, bool]] = queue.Queue(maxsize=1)
        self.errors: queue.Queue[str] = queue.Queue(maxsize=10)
        self._stop = threading.Event()
        self._audio_failed = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._streamer: Streamer | None = None
        self._audio: AudioSource | None = None
        self._temporary: tempfile.TemporaryDirectory | None = None
        self._camera_device: str | None = None
        self._curtain_path: str | None = None
        self._curtain_audio: Path | None = None
        self._curtain_active = False
        self._use_curtains = False
        self._frame_size = (1280, 720)

    @property
    def is_streaming(self) -> bool:
        with self._lock:
            return self._streamer is not None and self._streamer.is_streaming()

    @property
    def curtain_active(self) -> bool:
        return self._curtain_active

    def start_preview(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture, name="camera-capture", daemon=True)
        self._thread.start()

    def select_camera(self, device: str) -> None:
        self._camera_device = device

    def select_curtain(self, path: str) -> None:
        if self.is_streaming:
            raise ValueError("Detenga la transmisión antes de cambiar el archivo de cortinilla.")
        self._curtain_path = path
        self._curtain_active = False

    def toggle_curtain(self) -> bool:
        if platform.system() == "Windows":
            raise ValueError("Las cortinillas no están disponibles en Windows.")
        if not self._curtain_path:
            raise ValueError("Seleccione una cortinilla.")
        if self.is_streaming and not self._use_curtains:
            raise ValueError("Active la transmisión con cortinillas antes de iniciarla.")
        self._curtain_active = not self._curtain_active
        return self._curtain_active

    def start_stream(self, settings: StreamSettings) -> None:
        with self._lock:
            if self.is_streaming:
                return
            if self._camera_device is None or not settings.audio_device:
                raise ValueError("Seleccione un dispositivo de audio y video.")
            if not settings.destinations:
                raise ValueError("Seleccione al menos una plataforma.")
            if settings.use_curtains and platform.system() == "Windows":
                raise ValueError("Las cortinillas no están disponibles en Windows.")
            if self._curtain_active and not settings.use_curtains:
                raise ValueError("Active la transmisión con cortinillas o detenga la cortina.")
            streams = build_stream_targets(settings.destinations, settings.custom_server)
            # Dispose of an exited process before restarting the session.
            self.stop_stream()
            self._audio_failed.clear()
            self._use_curtains = settings.use_curtains
            try:
                if settings.use_curtains:
                    command = self._prepare_curtain_stream(settings, streams)
                else:
                    command = build_camera_stream_command(settings.audio_device, streams)
                streamer = Streamer(None, None, 30, command, on_stderr=self._handle_ffmpeg_output)
                self._streamer = streamer
                streamer.start_streaming()
            except Exception:
                self.stop_stream()
                raise

    def _prepare_curtain_stream(self, settings: StreamSettings, streams: str) -> list[str]:
        if not self._curtain_path:
            raise ValueError("Seleccione una cortinilla.")
        self._temporary = tempfile.TemporaryDirectory(prefix="streaming-")
        directory = Path(self._temporary.name)
        self._curtain_audio = directory / "curtain.wav"
        command = build_extract_audio_command(self._curtain_path, str(self._curtain_audio))
        # Both the clip and microphone must match the raw PCM input declared to FFmpeg.
        command[-1:-1] = ["-ac", str(AudioSource.channels), "-ar", str(AudioSource.rate)]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("No se pudo preparar el audio de la cortinilla.") from error
        self._audio = AudioSource(
            directory / "audio.pipe",
            settings.audio_device,
            lambda: self._curtain_audio if self._curtain_active else None,
            self._handle_audio_error,
        )
        self._audio.start()
        width, height = self._frame_size
        return build_curtain_stream_command(
            "-",
            str(self._audio.pipe_path),
            width,
            height,
            AudioSource.channels,
            AudioSource.rate,
            streams,
        )

    def stop_stream(self) -> None:
        with self._lock:
            streamer, self._streamer = self._streamer, None
            try:
                if streamer is not None:
                    streamer.stop_streaming()
            finally:
                audio, self._audio = self._audio, None
                temporary, self._temporary = self._temporary, None
                try:
                    if audio is not None:
                        audio.stop()
                finally:
                    self._curtain_audio = None
                    if temporary is not None:
                        temporary.cleanup()

    def close(self) -> None:
        self._stop.set()
        try:
            self.stop_stream()
        finally:
            if self._thread is not None and self._thread is not threading.current_thread():
                self._thread.join(timeout=2)

    def _handle_audio_error(self, message: str) -> None:
        self._audio_failed.set()
        self._report_error(message)

    def _stop_failed_stream(self, streamer: Streamer, message: str) -> None:
        # A delayed failure from the old process must not stop a newer transmission.
        with self._lock:
            if streamer is self._streamer:
                self._report_error(message)
                self.stop_stream()

    def _report_error(self, message: str) -> None:
        try:
            self.errors.put_nowait(message)
        except queue.Full:
            pass

    def _handle_ffmpeg_output(self, line: str) -> None:
        # FFmpeg diagnostics may contain stream keys. Only expose fixed messages.
        message = line.lower()
        if any(word in message for word in ("error", "failed", "broken pipe", "cannot")):
            self._report_error("FFmpeg no pudo enviar la transmisión a uno de los servidores.")

    def _publish_preview(self, frame: np.ndarray) -> None:
        try:
            self.preview_frames.get_nowait()
        except queue.Empty:
            pass
        try:
            self.preview_frames.put_nowait((frame, self._curtain_active))
        except queue.Full:
            pass

    @staticmethod
    def _open_camera(device: str):
        if device == BLACKMAGIC_DEVICE:
            if platform.system() != "Windows":
                return cv2.VideoCapture(DECKLINK_PIPELINE, cv2.CAP_GSTREAMER)
            return cv2.VideoCapture(0)
        return cv2.VideoCapture(int(device))

    def _capture(self) -> None:
        camera = None
        curtain = None
        selected_device = None
        selected_curtain = None
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                with self._lock:
                    streamer = self._streamer
                    stream_exited = streamer is not None and not streamer.is_streaming()
                if stream_exited or self._audio_failed.is_set():
                    self._audio_failed.clear()
                    if streamer is not None:
                        self._stop_failed_stream(
                            streamer,
                            "La transmisión se detuvo. Revise la conexión y los dispositivos.",
                        )
                if self._camera_device != selected_device:
                    if camera is not None:
                        camera.release()
                    selected_device = self._camera_device
                    camera = self._open_camera(selected_device)
                    if not camera.isOpened():
                        self._report_error("No se pudo abrir el dispositivo de video.")
                if camera is None or not camera.isOpened():
                    self._stop.wait(0.1)
                    continue
                success, camera_frame = camera.read()
                if not success:
                    self._stop.wait(0.05)
                    continue
                height, width = camera_frame.shape[:2]
                self._frame_size = (width, height)
                self._publish_preview(camera_frame)
                output_frame = camera_frame
                if self._curtain_active:
                    if curtain is None or selected_curtain != self._curtain_path:
                        if curtain is not None:
                            curtain.release()
                        selected_curtain = self._curtain_path
                        curtain = cv2.VideoCapture(selected_curtain)
                    success, curtain_frame = curtain.read()
                    if not success:
                        curtain.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        success, curtain_frame = curtain.read()
                    if success:
                        output_frame = adaptive_resize(curtain_frame, width, height, True)
                    else:
                        self._curtain_active = False
                        self._report_error("No se pudo leer el video de la cortinilla.")
                elif curtain is not None:
                    curtain.release()
                    curtain = None
                with self._lock:
                    streamer = self._streamer
                if streamer is not None:
                    if not streamer.is_streaming():
                        self._stop_failed_stream(
                            streamer, "La transmisión se detuvo. Revise la conexión y las claves."
                        )
                    else:
                        success, encoded = cv2.imencode(
                            ".jpg", output_frame, [cv2.IMWRITE_JPEG_QUALITY, 80]
                        )
                        if success:
                            try:
                                streamer.write(encoded.tobytes())
                            except (OSError, ValueError, RuntimeError):
                                self._stop_failed_stream(
                                    streamer, "Se perdió la conexión con la transmisión."
                                )
                self._stop.wait(max(0, 1 / 30 - (time.monotonic() - started)))
        except Exception:
            if not self._stop.is_set():
                self._report_error("Se interrumpió la captura de video.")
        finally:
            try:
                if camera is not None:
                    camera.release()
                if curtain is not None:
                    curtain.release()
            finally:
                self.stop_stream()
