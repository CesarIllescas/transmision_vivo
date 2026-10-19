"""Cancellable microphone and scene audio in a stable stereo PCM format."""

from __future__ import annotations

import errno
import os
import platform
import threading
import time
import unicodedata
import uuid
import wave
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from media.windows_pipe import WindowsAudioPipe

AUDIO_ERROR_MESSAGES = {
    "module": "No se pudo cargar PyAudio. Instale las dependencias de audio de la aplicación.",
    "init": "No se pudo inicializar el sistema de audio. Revise los dispositivos disponibles.",
    "device": "No se encontró el micrófono seleccionado. Vuelva a seleccionarlo en Audio y Video.",
    "open": (
        "No se pudo abrir el micrófono seleccionado. Revise sus permisos y que otra aplicación "
        "no lo esté usando en modo exclusivo."
    ),
    "rate": (
        "El micrófono rechazó la frecuencia de 44100 Hz. Revise su formato de audio "
        "o seleccione otro dispositivo."
    ),
    "read": "Se interrumpió la lectura del micrófono. Revise su conexión y permisos.",
    "pipe_create": "No se pudo crear el canal de audio para FFmpeg. Intente iniciar de nuevo.",
    "pipe_connect": "FFmpeg no pudo conectarse al canal de audio de la transmisión.",
    "pipe_write": "Se interrumpió el envío de audio a FFmpeg. Revise el estado de la transmisión.",
    "clip": (
        "No se pudo leer el audio de la cortinilla. Compruebe que contiene audio PCM "
        "estéreo de 44100 Hz."
    ),
    "thread": "No se pudo iniciar la captura de audio. Intente iniciar de nuevo.",
}


def _device_name_variants(name: str | bytes) -> tuple[str, ...]:
    """Retain the original and reversible Windows locale misdecodings of UTF-8."""
    if isinstance(name, bytes):
        for encoding in ("utf-8", "cp1252", "latin1"):
            try:
                name = name.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    variants = [name]
    for encoding in ("cp1252", "latin1"):
        try:
            repaired = name.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        if repaired not in variants:
            variants.append(repaired)
    return tuple(variants)


def _normalize_device_name(name: str) -> str:
    return unicodedata.normalize("NFC", " ".join(name.split())).casefold()


def _microphone_candidates(audio, device_name: str) -> list[tuple[int, int]]:
    """Prefer original names, then unambiguous encoding repairs or MME truncation."""
    wanted_variants = _device_name_variants(device_name)
    wanted = _normalize_device_name(wanted_variants[0])
    wanted_forms = {_normalize_device_name(name) for name in wanted_variants}
    exact, repaired, truncated = [], [], []
    for index in range(audio.get_device_count()):
        device = audio.get_device_info_by_index(index)
        channels = min(2, int(device["maxInputChannels"]))
        if channels <= 0:
            continue
        variants = _device_name_variants(device["name"])
        name = _normalize_device_name(variants[0])
        forms = {_normalize_device_name(variant) for variant in variants}
        host = device.get("hostApi")
        host_type = audio.get_host_api_info_by_index(host)["type"] if host is not None else 0
        # Full WASAPI names avoid MME's 31-character device name limitation.
        priority = {13: 0, 1: 1, 2: 2}.get(host_type, 3)
        candidate = (priority, index, channels, host_type, name)
        if name == wanted:
            exact.append(candidate)
        elif forms & wanted_forms:
            repaired.append(candidate)
        elif (
            host_type == 2
            and any(len(variant) == 31 for variant in variants)
            and any(target.startswith(prefix) for target in wanted_forms for prefix in forms)
        ):
            truncated.append(candidate)
    # Different original names or multiple entries in one host can refer to different
    # physical microphones. An encoding repair must not silently choose between them.
    if repaired and (
        len({candidate[4] for candidate in repaired}) > 1
        or len({candidate[3] for candidate in repaired}) != len(repaired)
    ):
        repaired = []
        truncated = []
    matches = exact or repaired or (truncated if len(truncated) == 1 else [])
    return [(index, channels) for _, index, channels, _, _ in sorted(matches)]


class AudioSource:
    """Own a microphone and pipe; switching scenes never changes the PCM format."""

    channels = 2
    rate = 44100
    chunk = 1024

    def __init__(
        self,
        pipe_path: str | Path,
        device_name: str,
        curtain_audio: Callable[[], Path | None],
        on_error: Callable[[str], None],
        *,
        curtain_position: Callable[[], tuple[Path, float] | None] | None = None,
        stream_started_at: Callable[[], float | None] | None = None,
    ) -> None:
        self._windows = platform.system() == "Windows"
        self.pipe_path = (
            rf"\\.\pipe\streaming-{uuid.uuid4().hex}" if self._windows else str(pipe_path)
        )
        self.device_name = device_name
        self.curtain_audio = curtain_audio
        self.on_error = on_error
        self.curtain_position = curtain_position
        self.stream_started_at = stream_started_at
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._windows_pipe: WindowsAudioPipe | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        try:
            if self._windows:
                self._windows_pipe = WindowsAudioPipe(self.pipe_path, self._stop)
            else:
                os.mkfifo(self.pipe_path)
        except OSError:
            raise ValueError(AUDIO_ERROR_MESSAGES["pipe_create"]) from None
        try:
            self._thread = threading.Thread(target=self._run, name="curtain-audio", daemon=True)
            self._thread.start()
        except Exception:
            self._thread = None
            if self._windows_pipe is not None:
                with suppress(OSError):
                    self._windows_pipe.close()
            raise ValueError(AUDIO_ERROR_MESSAGES["thread"]) from None

    def stop(self) -> None:
        self._stop.set()
        if self._windows_pipe is not None:
            self._windows_pipe.cancel()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _open_pipe(self) -> int | WindowsAudioPipe | None:
        if self._windows_pipe is not None:
            return self._windows_pipe if self._windows_pipe.connect() else None
        # A blocking open would hang forever when FFmpeg fails before opening its input.
        while not self._stop.is_set():
            try:
                return os.open(self.pipe_path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as error:
                if error.errno != errno.ENXIO:
                    raise
                self._stop.wait(0.05)
        return None

    def _write(self, descriptor: int | WindowsAudioPipe, data: bytes) -> None:
        if isinstance(descriptor, WindowsAudioPipe):
            descriptor.write(data)
            return
        remaining = memoryview(data)
        while remaining and not self._stop.is_set():
            try:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise BrokenPipeError("The audio pipe stopped accepting data")
                remaining = remaining[written:]
            except BlockingIOError:
                self._stop.wait(0.01)

    def _read_microphone(self, microphone, channels: int, *, discard: bool) -> bytes:
        """Read only available samples and discard backlog while a scene is on air."""
        available = microphone.get_read_available()
        if available < 0:
            raise OSError("The microphone is unavailable")
        # Keep a small cushion for device callback jitter; only trim larger backlog.
        skip = available if discard else max(0, available - self.chunk * 2)
        readable = available - skip
        while skip and not self._stop.is_set():
            count = min(skip, self.chunk * 8)
            microphone.read(count, exception_on_overflow=False)
            skip -= count
        if discard or self._stop.is_set():
            return b""
        frames = min(readable, self.chunk)
        if not frames:
            return bytes(self.chunk * self.channels * 2)
        data = microphone.read(frames, exception_on_overflow=False)
        if channels == 1:
            import numpy as np

            data = np.repeat(np.frombuffer(data, dtype=np.int16), 2).tobytes()
        return data.ljust(self.chunk * self.channels * 2, b"\0")

    def _prime_timeline(self, descriptor: int | WindowsAudioPipe) -> float:
        """Align the first PCM sample with video even if FFmpeg opens audio later."""
        connected_at = time.monotonic()
        anchor = self.stream_started_at() if self.stream_started_at else None
        if anchor is None or anchor >= connected_at:
            return connected_at
        padding_frames = round((connected_at - anchor) * self.rate)
        remaining = padding_frames
        while remaining and not self._stop.is_set():
            frames = min(remaining, self.chunk)
            self._write(descriptor, bytes(frames * self.channels * 2))
            remaining -= frames
        return anchor + padding_frames / self.rate

    def _read_clip(self, clip, position: float | None) -> bytes:
        length = clip.getnframes()
        if not length:
            raise ValueError("La cortinilla no contiene audio.")
        if position is not None:
            target = max(0, int(position * self.rate))
            if target >= length:
                return bytes(self.chunk * self.channels * 2)
            if abs(clip.tell() - target) > self.chunk * 2 or target == 0:
                clip.setpos(target)
        data = clip.readframes(self.chunk)
        if position is None and not data:
            clip.rewind()
            data = clip.readframes(self.chunk)
        return data.ljust(self.chunk * self.channels * 2, b"\0")

    def _run(self) -> None:
        audio = None
        microphone = None
        descriptor = None
        clip = None
        clip_path = None
        stage = "module"
        try:
            if self._stop.is_set():
                return
            # Only curtain-enabled transmissions need a PortAudio device.
            import pyaudio

            stage = "init"
            audio = pyaudio.PyAudio()
            stage = "device"
            candidates = _microphone_candidates(audio, self.device_name)
            if not candidates:
                raise ValueError(AUDIO_ERROR_MESSAGES[stage])
            stage = "open"
            input_channels = self.channels
            invalid_rate = True
            for device_index, candidate_channels in candidates:
                try:
                    microphone = audio.open(
                        format=pyaudio.paInt16,
                        channels=candidate_channels,
                        rate=self.rate,
                        input=True,
                        frames_per_buffer=self.chunk,
                        input_device_index=device_index,
                    )
                    input_channels = candidate_channels
                    break
                except OSError as error:
                    invalid_rate = invalid_rate and error.errno == -9997
                    continue
            if microphone is None:
                stage = "rate" if invalid_rate else "open"
                raise ValueError(AUDIO_ERROR_MESSAGES[stage])
            stage = "pipe_connect"
            descriptor = self._open_pipe()
            if descriptor is None:
                return
            block_duration = self.chunk / self.rate
            stage = "pipe_write"
            next_block_at = self._prime_timeline(descriptor)
            while not self._stop.is_set():
                stage = "clip"
                selection = self.curtain_position() if self.curtain_position else None
                if self.curtain_position:
                    selected_path, position = selection if selection else (None, None)
                else:
                    selected_path, position = self.curtain_audio(), None
                if selected_path != clip_path:
                    if clip is not None:
                        clip.close()
                    clip = wave.open(str(selected_path), "rb") if selected_path else None
                    clip_path = selected_path
                    if clip is not None:
                        if (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) != (
                            self.channels,
                            2,
                            self.rate,
                        ):
                            raise ValueError(
                                "La cortinilla requiere audio PCM estéreo de 44100 Hz."
                            )
                        if position is not None:
                            clip.setpos(min(clip.getnframes(), max(0, int(position * self.rate))))
                if clip is not None:
                    stage = "read"
                    self._read_microphone(microphone, input_channels, discard=True)
                    stage = "clip"
                    data = self._read_clip(clip, position)
                else:
                    stage = "read"
                    data = self._read_microphone(microphone, input_channels, discard=False)
                stage = "pipe_write"
                self._write(descriptor, data)
                # Absolute deadlines compensate Windows timer rounding instead of
                # accumulating one oversleep on every 1024-sample block.
                next_block_at += block_duration
                now = time.monotonic()
                if now - next_block_at > 0.5:
                    # A stalled reader should not trigger an unbounded catch-up burst.
                    next_block_at = now
                self._stop.wait(max(0, next_block_at - now))
        except Exception:
            if not self._stop.is_set():
                self.on_error(AUDIO_ERROR_MESSAGES[stage])
        finally:
            if clip is not None:
                with suppress(Exception):
                    clip.close()
            if self._windows_pipe is not None:
                with suppress(OSError):
                    self._windows_pipe.close()
            elif descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            if microphone is not None:
                with suppress(Exception):
                    microphone.stop_stream()
                with suppress(Exception):
                    microphone.close()
            if audio is not None:
                with suppress(Exception):
                    audio.terminate()
