"""Scene timing and decoding tests without opening files or capture devices."""

import math
import unittest
from unittest.mock import Mock, patch

import cv2

from media.curtain import CurtainVideo


class CurtainVideoTests(unittest.TestCase):
    def setUp(self):
        self.capture = Mock()
        self.capture.grab.return_value = True
        self.capture.set.return_value = True
        self.capture.read.return_value = (True, object())
        self.enterContext(patch("media.curtain.cv2.VideoCapture", return_value=self.capture))

    def open_clip(self, fps=60, frame_count=120):
        metadata = {cv2.CAP_PROP_FPS: fps, cv2.CAP_PROP_FRAME_COUNT: frame_count}
        self.capture.get.side_effect = metadata.__getitem__
        clip = CurtainVideo("synthetic.mp4")
        self.addCleanup(clip.close)
        return clip

    def test_slow_source_repeats_frames_until_the_next_source_frame(self):
        clip = self.open_clip(fps=24, frame_count=48)
        first = clip.frame_at(0)
        self.assertIs(clip.frame_at(1 / 30), first)
        self.capture.read.assert_called_once()
        next_frame = object()
        self.capture.read.return_value = True, next_frame
        self.assertIs(clip.frame_at(2 / 30), next_frame)
        self.assertEqual(self.capture.read.call_count, 2)
        self.capture.grab.assert_not_called()
        self.capture.set.assert_not_called()

    def test_fast_source_skips_sequentially_without_repeated_seeks(self):
        clip = self.open_clip()
        clip.frame_at(1 / 30)
        clip.frame_at(2 / 30)
        self.assertEqual(self.capture.grab.call_count, 2)
        self.assertEqual(self.capture.read.call_count, 3)
        self.capture.set.assert_not_called()
        self.assertEqual(clip.duration, 2)

    def test_large_time_jump_seeks_once_and_continues_reading(self):
        clip = self.open_clip()
        clip.frame_at(1.0)
        self.capture.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 60)
        clip.frame_at(1.0 + 1 / 30)
        self.capture.set.assert_called_once()
        self.capture.grab.assert_called_once()

    def test_loop_wrap_seeks_to_beginning_and_keeps_original_duration(self):
        clip = self.open_clip(fps=25, frame_count=50)
        clip.frame_at(1.96)
        self.capture.set.reset_mock()
        clip.frame_at(2.01)
        self.capture.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 0)
        self.assertEqual(clip.duration, 2)

    def test_failed_sequential_skip_is_reported(self):
        clip = self.open_clip()
        self.capture.grab.return_value = False
        with self.assertRaises(ValueError):
            clip.frame_at(1 / 30)
        self.capture.read.assert_called_once()

    def test_failed_seek_is_reported_before_reading_the_wrong_frame(self):
        clip = self.open_clip()
        self.capture.set.return_value = False
        with self.assertRaises(ValueError):
            clip.frame_at(1.0)
        self.capture.read.assert_called_once()

    def test_failed_decode_is_reported(self):
        clip = self.open_clip()
        self.capture.read.return_value = False, None
        with self.assertRaises(ValueError):
            clip.frame_at(1 / 60)

    def test_invalid_source_metadata_releases_capture(self):
        for fps, count in ((0, 120), (math.nan, 120), (30, 0), (30, math.inf)):
            with self.subTest(fps=fps, count=count):
                self.capture.release.reset_mock()
                self.capture.get.side_effect = {
                    cv2.CAP_PROP_FPS: fps,
                    cv2.CAP_PROP_FRAME_COUNT: count,
                }.__getitem__
                with self.assertRaises(ValueError):
                    CurtainVideo("invalid.mp4")
                self.capture.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
