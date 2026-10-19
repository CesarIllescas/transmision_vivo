"""Read looping video at its source frame rate using a shared scene clock."""

from __future__ import annotations

import math

import cv2
import numpy as np

MAX_SEQUENTIAL_ADVANCE = 8


class CurtainVideo:
    def __init__(self, path: str) -> None:
        self._capture = cv2.VideoCapture(path)
        self._index = 0
        try:
            self.fps = self._capture.get(cv2.CAP_PROP_FPS)
            frame_count = self._capture.get(cv2.CAP_PROP_FRAME_COUNT)
            if not math.isfinite(self.fps) or self.fps <= 0:
                raise ValueError("No se pudo determinar la velocidad de la cortinilla.")
            if not math.isfinite(frame_count) or frame_count < 1:
                raise ValueError("La cortinilla no contiene fotogramas válidos.")
            self.frame_count = int(frame_count)
            self.duration = self.frame_count / self.fps
            success, self._frame = self._capture.read()
            if not success:
                raise ValueError("No se pudo leer el video de la cortinilla.")
        except Exception:
            self.close()
            raise

    def frame_at(self, elapsed: float) -> np.ndarray:
        """Repeat or skip source frames so a 24/25/60 fps clip keeps its duration."""
        position = max(0.0, elapsed) % self.duration
        index = min(int(position * self.fps), self.frame_count - 1)
        if index != self._index:
            advance = index - self._index
            if 0 < advance <= MAX_SEQUENTIAL_ADVANCE:
                # A 60 fps clip rendered at 30 fps normally advances two frames.
                # Decode forward instead of seeking back to a keyframe every time.
                for _ in range(advance - 1):
                    if not self._capture.grab():
                        raise ValueError("Se interrumpió la lectura de la cortinilla.")
            elif not self._capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise ValueError("No se pudo cambiar la posición de la cortinilla.")
            success, frame = self._capture.read()
            if not success:
                raise ValueError("Se interrumpió la lectura de la cortinilla.")
            self._frame = frame
            self._index = index
        return self._frame

    def close(self) -> None:
        self._capture.release()
