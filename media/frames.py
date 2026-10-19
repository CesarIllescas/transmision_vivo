"""Frame conversion and aspect-ratio-preserving preview resizing."""

import cv2
import numpy as np


def adaptive_resize(frame, target_width, target_height, performance_mode=False):
    """Fit a BGR frame into the requested canvas and pad the remainder in black."""
    if target_width <= 0 or target_height <= 0:
        raise ValueError("Frame dimensions must be positive")
    original_height, original_width = frame.shape[:2]
    if original_width == 0 or original_height == 0:
        raise ValueError("Cannot resize an empty frame")

    aspect_ratio = original_width / original_height
    if aspect_ratio > target_width / target_height:
        new_width = target_width
        new_height = max(1, int(target_width / aspect_ratio))
    else:
        new_height = target_height
        new_width = max(1, int(target_height * aspect_ratio))

    interpolation = cv2.INTER_NEAREST if performance_mode else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (new_width, new_height), interpolation=interpolation)
    canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
    x_offset = (target_width - new_width) // 2
    y_offset = (target_height - new_height) // 2
    canvas[y_offset : y_offset + new_height, x_offset : x_offset + new_width] = resized
    return canvas


def convert_to_frame(raw_frame, width, height):
    """View packed BGR bytes as a frame without copying or JIT compilation."""
    return np.frombuffer(raw_frame, np.uint8).reshape((height, width, 3))
