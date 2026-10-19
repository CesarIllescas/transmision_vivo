"""Cancellable PCM audio source for the macOS curtain pipeline."""

from __future__ import annotations

import errno
import os
import threading
import time
import wave
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path


class AudioSource:
    """Own a microphone and FIFO; switching scenes never changes the PCM format."""

    channels = 2
    rate = 44100
    chunk = 1024

    def __init__(
        self,
        pipe_path: Path,
        device_name: str,
        curtain_audio: Callable[[], Path | None],
        on_error: Callable[[str], None],
    ) -> None:
        self.pipe_path = pipe_path
        self.device_name = device_name
        self.curtain_audio = curtain_audio
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        os.mkfifo(self.pipe_path)
        self._thread = threading.Thread(target=self._run, name="curtain-audio", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _open_pipe(self) -> int | None:
        # A blocking open would hang forever when FFmpeg fails before opening its input.
        while not self._stop.is_set():
            try:
                return os.open(self.pipe_path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as error:
                if error.errno != errno.ENXIO:
                    raise
                self._stop.wait(0.05)
        return None

    def _write(self, descriptor: int, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining and not self._stop.is_set():
            try:
                written = os.write(descriptor, remaining)
                if written == 0:
                    raise BrokenPipeError("The audio pipe stopped accepting data")
                remaining = remaining[written:]
            except BlockingIOError:
                self._stop.wait(0.01)

    def _run(self) -> None:
        audio = None
        microphone = None
        descriptor = None
        clip = None
        clip_path = None
        try:
            # Only curtain-enabled transmissions need a PortAudio device.
            import pyaudio

            audio = pyaudio.PyAudio()
            device_index = None
            input_channels = self.channels
            for index in range(audio.get_device_count()):
                device = audio.get_device_info_by_index(index)
                if device["name"] == self.device_name and device["maxInputChannels"] > 0:
                    device_index = index
                    input_channels = min(self.channels, int(device["maxInputChannels"]))
                    break
            if device_index is None:
                raise ValueError("El dispositivo de audio seleccionado no está disponible.")
            microphone = audio.open(
                format=pyaudio.paInt16,
                channels=input_channels,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk,
                input_device_index=device_index,
            )
            descriptor = self._open_pipe()
            if descriptor is None:
                return
            while not self._stop.is_set():
                started = time.monotonic()
                selected_path = self.curtain_audio()
                if selected_path != clip_path:
                    if clip is not None:
                        clip.close()
                    clip = wave.open(str(selected_path), "rb") if selected_path else None
                    clip_path = selected_path
                if clip is not None:
                    data = clip.readframes(self.chunk)
                    if not data:
                        clip.rewind()
                        data = clip.readframes(self.chunk)
                    if not data:
                        raise ValueError("La cortinilla no contiene audio.")
                else:
                    data = microphone.read(self.chunk, exception_on_overflow=False)
                    if input_channels == 1:
                        # Duplicate each 16-bit mono sample to preserve the stereo pipe format.
                        import numpy as np

                        data = np.repeat(np.frombuffer(data, dtype=np.int16), 2).tobytes()
                self._write(descriptor, data)
                self._stop.wait(max(0, self.chunk / self.rate - (time.monotonic() - started)))
        except Exception:
            if not self._stop.is_set():
                self.on_error("No se pudo capturar o enviar el audio de la transmisión.")
        finally:
            if clip is not None:
                with suppress(Exception):
                    clip.close()
            if descriptor is not None:
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
