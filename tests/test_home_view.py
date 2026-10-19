"""Home view lifecycle tests with real Flet controls and no media devices."""

import asyncio
import json
import queue
import unittest
from itertools import count
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import flet as ft
import numpy as np
from flet_core.connection import Connection

from db.db_conn import TransmissionRepository
from media.session import StreamingSession
from pages.home_view import HomeScreen, encode_preview
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
        self.session.curtain_active = False
        self.session.preview_frames = queue.Queue()
        self.session.live_frames = queue.Queue()
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
        self.assertTrue(self.screen.preview_video.muted)

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
        first_player = self.screen.preview_video
        self.session.toggle_curtain.side_effect = lambda: setattr(
            self.session, "curtain_active", not self.session.curtain_active
        )
        self.screen._toggle_curtain(None)

        self.assertIs(self.screen.live_container.content, self.screen.live_image)
        self.assertIs(self.screen.live_image.page, self.page)
        self.assertIs(self.screen.preview_video.page, None)
        self.assertIs(self.screen.preview_container.content, self.screen.preview_image)

        self.screen._toggle_curtain(None)
        self.assertIs(self.screen.live_container.content, self.screen.live_image)
        self.assertIs(self.screen.preview_video.page, self.page)

        self.screen._select_curtain(SimpleNamespace(control=SimpleNamespace(key="second.mp4")))
        self.assertIsNot(self.screen.preview_video, first_player)
        self.assertIsNone(first_player.page)
        self.assertEqual(len(self.screen.preview_video.playlist), 1)
        self.assertEqual(self.screen.preview_video.playlist[0].resource, "second.mp4")
        self.session.select_curtain.assert_called_with("second.mp4")

    def test_selection_sends_initial_media_without_waiting_for_native_player(self):
        self.mount()
        with patch.object(
            self.connection,
            "send_command",
            side_effect=AssertionError("Native player has not registered its methods yet"),
        ):
            self.screen._select_curtain(SimpleNamespace(control=SimpleNamespace(key="first.mp4")))
            self.session.curtain_active = True
            self.screen._sync_curtain_controls()
            self.session.curtain_active = False
            self.screen._sync_curtain_controls()
            self.screen._select_curtain(SimpleNamespace(control=SimpleNamespace(key="second.mp4")))

        video_commands = [
            child
            for command in self.connection.commands
            if command.name == "add"
            for child in command.commands
            if child.values == ["video"]
        ]
        self.assertEqual(len(video_commands), 3)
        self.assertEqual(
            [json.loads(command.attrs["playlist"])[0]["resource"] for command in video_commands],
            ["first.mp4", "first.mp4", "second.mp4"],
        )
        for command in video_commands:
            self.assertEqual(command.attrs["autoplay"], "true")
            self.assertEqual(command.attrs["muted"], "true")

    def test_file_picker_selects_first_readable_clip_and_keeps_later_selection(self):
        self.mount()
        invalid = Mock()
        invalid.read.return_value = (False, None)
        valid = Mock()
        valid.read.return_value = (True, np.zeros((24, 32, 3), dtype=np.uint8))
        files = [SimpleNamespace(path=path) for path in ("invalid.mp4", "first.mp4", "next.mp4")]
        with patch("pages.home_view.cv2.VideoCapture", side_effect=[invalid, valid, valid]):
            self.screen._add_files(SimpleNamespace(files=files))

        self.assertEqual(self.screen._selected_curtain, "first.mp4")
        self.assertEqual(self.screen.preview_video.playlist[0].resource, "first.mp4")
        self.assertEqual(len(self.screen.curtain_items.controls), 2)
        invalid.release.assert_called_once()
        self.assertEqual(valid.release.call_count, 2)
        first_player = self.screen.preview_video

        with patch("pages.home_view.cv2.VideoCapture", return_value=valid):
            self.screen._add_files(SimpleNamespace(files=[SimpleNamespace(path="third.mp4")]))

        self.assertEqual(self.screen._selected_curtain, "first.mp4")
        self.assertIs(self.screen.preview_video, first_player)
        self.session.select_curtain.assert_called_once_with("first.mp4")

    def test_preview_error_has_fixed_message_and_ignores_disposed_players(self):
        self.mount()
        self.screen._select_curtain_path("first.mp4")
        old_player = self.screen.preview_video
        self.screen._select_curtain_path("second.mp4")

        self.screen._preview_error(SimpleNamespace(control=old_player, data="private details"))
        self.router.set_dialog.assert_not_called()
        self.screen._preview_error(
            SimpleNamespace(control=self.screen.preview_video, data="private details")
        )

        dialog = self.router.set_dialog.call_args.args[0]
        self.assertEqual(
            dialog.content.value,
            "No se pudo reproducir la vista previa del video seleccionado.",
        )

    def refresh_once(self):
        async def finish_tick(_delay):
            self.screen._close_session()

        with patch("pages.home_view.asyncio.sleep", side_effect=finish_tick):
            self.loop.run_until_complete(self.screen._refresh_preview())

    def test_refresh_shows_engine_output_live_and_camera_off_air(self):
        self.mount()
        self.session.curtain_active = True
        camera_frame = np.zeros((2, 2, 3), dtype=np.uint8)
        live_frame = np.full((2, 2, 3), 255, dtype=np.uint8)
        self.session.preview_frames.put((camera_frame, True))
        self.session.live_frames.put(live_frame)

        self.refresh_once()

        self.assertIs(self.screen.live_container.content, self.screen.live_image)
        self.assertEqual(self.screen.live_image.src_base64, encode_preview(live_frame))
        self.assertIs(self.screen.preview_container.content, self.screen.preview_image)
        self.assertEqual(self.screen.preview_image.src_base64, encode_preview(camera_frame))
        self.assertEqual(self.screen.curtain_button.text, "Detener cortina")

    def test_refresh_restores_off_air_clip_when_capture_disables_curtain(self):
        self.mount()
        self.screen._select_curtain(SimpleNamespace(control=SimpleNamespace(key="clip.mp4")))
        self.session.curtain_active = True
        self.screen._sync_curtain_controls()
        self.session.curtain_active = False
        self.session.errors.put("No se pudo leer el video de la cortinilla.")

        self.refresh_once()

        self.assertIs(self.screen.preview_container.content, self.screen.preview_video)
        self.assertEqual(self.screen.curtain_button.text, "Iniciar cortina")
        self.assertTrue(self.screen.preview_video.muted)
        self.router.set_dialog.assert_called_once()

    def test_refresh_never_queries_stream_status_while_start_is_busy(self):
        self.mount()
        self.screen._streaming = True
        self.screen._stream_lock.acquire()
        try:
            with patch.object(
                type(self.session), "is_streaming", new_callable=PropertyMock, create=True
            ) as stream_status:
                stream_status.side_effect = AssertionError("Would block on session lock")
                self.refresh_once()
                stream_status.assert_not_called()
        finally:
            self.screen._stream_lock.release()

    def test_preparing_stream_locks_settings_scenes_and_pending_handlers(self):
        self.mount()

        def start_stream(_settings):
            for control in (
                self.screen.camera_select,
                self.screen.audio_select,
                self.screen.curtain_switch,
                self.screen.file_button,
                self.screen.curtain_items,
                self.screen.curtain_button,
                self.screen.stream_button,
                *self.screen.platform_checks.values(),
                *self.screen.platform_keys.values(),
            ):
                self.assertTrue(control.disabled)
            self.screen._change_camera(SimpleNamespace(control=SimpleNamespace(value="1")))
            self.screen._select_curtain(SimpleNamespace(control=SimpleNamespace(key="clip.mp4")))
            self.screen._toggle_curtain(None)
            self.session.is_streaming = True

        self.session.start_stream.side_effect = start_stream

        self.screen._toggle_stream(None)

        self.session.start_stream.assert_called_once()
        self.session.select_camera.assert_not_called()
        self.session.select_curtain.assert_not_called()
        self.session.toggle_curtain.assert_not_called()
        self.assertFalse(self.screen.stream_button.disabled)
        self.assertFalse(self.screen.curtain_button.disabled)
        self.assertTrue(self.screen.curtain_items.disabled)
        self.assertFalse(self.screen._stream_lock.locked())

    def test_close_during_start_stops_result_and_does_not_persist_settings(self):
        self.mount()

        def start_stream(_settings):
            self.screen._confirm_close(None)
            self.session.is_streaming = True

        self.session.start_stream.side_effect = start_stream

        self.screen._toggle_stream(None)

        self.session.close.assert_called_once()
        self.session.stop_stream.assert_called_once()
        self.repository.save_keys.assert_not_called()
        self.assertFalse(self.screen._streaming)
        self.assertFalse(self.screen._stream_lock.locked())
        self.router.destroy.assert_called_once()

    def test_closed_view_does_not_start_stream(self):
        self.mount()
        self.screen._close_session()

        self.screen._toggle_stream(None)

        self.session.start_stream.assert_not_called()

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
