"""Home view lifecycle tests with real Flet controls and no media devices."""

import asyncio
import queue
import unittest
from itertools import count
from types import SimpleNamespace
from unittest.mock import Mock, patch

import flet as ft
from flet_core.connection import Connection

from db.db_conn import TransmissionRepository
from media.session import StreamingSession
from pages.home_view import HomeScreen
from pages.Router import Router


class MemoryConnection(Connection):
    """Acknowledge Flet commands without starting a desktop or browser client."""

    def __init__(self):
        super().__init__()
        self.commands = []
        self.control_ids = count(1)

    def send_commands(self, session_id, commands):
        self.commands.extend(commands)
        results = []
        for command in commands:
            if command.name == "add":
                results.append(
                    " ".join(
                        control.attrs.get("id") or f"control-{next(self.control_ids)}"
                        for control in command.commands
                    )
                )
        return SimpleNamespace(results=results)

    def send_command(self, session_id, command):
        self.commands.append(command)
        return SimpleNamespace(error="", result=None)


class HomeScreenTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        self.connection = MemoryConnection()
        self.page = ft.Page(self.connection, "home-view-test", self.loop)
        self.page.run_task = Mock()
        self.router = Mock(spec=Router)
        self.router.page = self.page
        self.router.set_overlay.side_effect = self.add_overlay
        self.repository = Mock(spec=TransmissionRepository)
        self.repository.load_keys.return_value = {"Youtube": "saved-key"}
        self.session = Mock(spec=StreamingSession)
        self.session.is_streaming = False
        self.session.preview_frames = queue.Queue()
        self.session.errors = queue.Queue()
        self.screen = HomeScreen(self.router, self.repository, self.session)
        self.devices = self.enterContext(
            patch("pages.home_view.list_devices", return_value=(["Camera"], ["Microphone"]))
        )
        self.environment = self.enterContext(patch("pages.home_view.set_environment_paths"))
        self.addCleanup(self.unmount)

    def add_overlay(self, control):
        self.page.overlay.append(control)
        self.page.update()

    def mount(self):
        self.page.add(self.screen)

    def unmount(self):
        if self.screen in self.page.controls:
            self.page.remove(self.screen)

    def test_constructing_and_building_does_not_start_media(self):
        self.repository.load_keys.assert_not_called()
        self.session.start_preview.assert_not_called()

        self.screen.build()

        self.assertIsInstance(self.screen.controls[0], ft.Column)
        self.assertEqual(self.screen.platform_keys["Youtube"].value, "saved-key")
        self.assertTrue(self.screen.platform_keys["Youtube"].disabled)
        self.session.start_preview.assert_not_called()
        self.page.run_task.assert_not_called()
        self.devices.assert_not_called()

    def test_mount_and_unmount_own_preview_and_file_picker(self):
        self.mount()

        self.assertIs(self.screen.page, self.page)
        self.assertIs(self.screen.file_picker.page, self.page)
        self.session.start_preview.assert_called_once()
        self.page.run_task.assert_called_once_with(self.screen._refresh_preview)
        self.router.set_prevent_close.assert_called_once_with(True)
        self.router.set_on_close.assert_called_once_with(self.screen._window_event)
        self.assertEqual(self.screen.camera_select.options[0].text, "Camera")
        self.assertEqual(self.screen.audio_select.options[0].key, "Microphone")

        self.unmount()
        self.screen._close_session()

        self.session.close.assert_called_once()
        self.assertTrue(self.screen._closed)
        self.assertNotIn(self.screen.file_picker, self.page.overlay)

    def test_cancelled_file_picker_does_not_open_video(self):
        self.mount()
        with patch("pages.home_view.cv2.VideoCapture") as capture:
            self.screen._add_files(SimpleNamespace(files=None))

        capture.assert_not_called()
        self.assertEqual(self.screen.curtain_items.controls, [])

    def test_selecting_and_switching_scenes_uses_mounted_players(self):
        self.mount()
        self.screen._select_curtain(SimpleNamespace(control=SimpleNamespace(key="first.mp4")))

        self.assertIs(self.screen.preview_video.page, self.page)
        self.assertEqual(self.screen.preview_video.playlist[0].resource, "first.mp4")
        self.session.toggle_curtain.return_value = True
        self.screen._toggle_curtain(None)

        self.assertIs(self.screen.live_video.page, self.page)
        self.assertIs(self.screen.preview_video.page, None)
        self.assertIs(self.screen.preview_container.content, self.screen.preview_image)
        self.assertEqual(self.screen.live_video.playlist[0].resource, "first.mp4")

        self.session.toggle_curtain.return_value = False
        self.screen._toggle_curtain(None)
        self.assertIs(self.screen.live_container.content, self.screen.live_image)
        self.assertIs(self.screen.preview_video.page, self.page)

        self.screen._select_curtain(SimpleNamespace(control=SimpleNamespace(key="second.mp4")))
        self.assertEqual(len(self.screen.preview_video.playlist), 1)
        self.assertEqual(self.screen.preview_video.playlist[0].resource, "second.mp4")
        self.session.select_curtain.assert_called_with("second.mp4")

    def test_rejected_scene_keeps_existing_preview(self):
        self.mount()
        self.session.select_curtain.side_effect = ValueError("Stop the stream first")

        self.screen._select_curtain(SimpleNamespace(control=SimpleNamespace(key="clip.mp4")))

        self.assertIsNone(self.screen._selected_curtain)
        self.assertIsNone(self.screen.preview_container.content)
        self.router.set_dialog.assert_called_once()

    def test_failed_device_discovery_shows_error_without_aborting_mount(self):
        self.devices.side_effect = OSError("FFmpeg unavailable")

        self.mount()

        self.router.set_dialog.assert_called_once()
        self.session.start_preview.assert_called_once()

    def test_confirmed_close_releases_session_once_before_window_destroy(self):
        self.mount()
        order = Mock()
        order.attach_mock(self.session.close, "close_session")
        order.attach_mock(self.router.destroy, "destroy_window")

        self.screen._confirm_close(None)
        self.unmount()

        self.assertEqual(
            [call[0] for call in order.mock_calls], ["close_session", "destroy_window"]
        )


if __name__ == "__main__":
    unittest.main()
