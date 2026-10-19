"""Live streaming controls; media resources belong to StreamingSession."""

from __future__ import annotations

import asyncio
import base64
import queue
import threading
from pathlib import Path

import cv2
import flet as ft
import numpy as np

from db.db_conn import TransmissionRepository
from logger.logs import log
from media.commands import set_environment_paths
from media.devices import list_devices
from media.session import BLACKMAGIC_DEVICE, StreamingSession, StreamSettings
from pages.Router import Router

PLATFORMS = ("Facebook", "Youtube", "X", "Otro")


def encode_preview(frame: np.ndarray) -> str:
    success, encoded = cv2.imencode(".png", frame)
    if not success:
        raise ValueError("No se pudo generar la vista previa.")
    return base64.b64encode(encoded).decode("ascii")


class HomeScreen(ft.Column):
    def __init__(
        self,
        router: Router,
        repository: TransmissionRepository | None = None,
        session: StreamingSession | None = None,
    ) -> None:
        super().__init__()
        self.router = router
        self.repository = repository if repository is not None else TransmissionRepository()
        self.session = session if session is not None else StreamingSession()
        self._refresh_task = None
        self._closed = False
        self._stream_lock = threading.Lock()
        self._selected_curtain: str | None = None
        self._streaming = False

    def is_isolated(self) -> bool:
        return True

    def build(self) -> None:
        self.controls = [self._build_content()]

    def _build_content(self) -> ft.Column:
        keys = self.repository.load_keys()
        self.platform_checks = {
            name: ft.Checkbox(label=name, height=45, on_change=self._change_platform)
            for name in PLATFORMS
        }
        self.platform_keys = {
            name: ft.TextField(
                label=name, height=60, width=230, disabled=True, value=keys.get(name, "")
            )
            for name in PLATFORMS
        }
        self.custom_server = ft.TextField(width=380, label="Ruta del servidor", disabled=True)
        self.camera_select = ft.Dropdown(
            width=380, label="Dispositivo de video", on_change=self._change_camera
        )
        self.audio_select = ft.Dropdown(width=380, label="Dispositivo de audio")
        self.curtain_switch = ft.Switch(label="Transmisión con cortinillas", value=False)
        blank = encode_preview(np.zeros((1, 1, 3), dtype=np.uint8))
        self.live_image = ft.Image(src_base64=blank, border_radius=20)
        self.preview_image = ft.Image(src_base64=blank, border_radius=20)
        self.preview_video = self._video_player()
        self.live_video = self._video_player(show_controls=False)
        self.live_container = ft.Container(
            bgcolor=ft.colors.BLACK, width=700, height=350, content=self.live_image
        )
        self.preview_container = ft.Container(bgcolor=ft.colors.BLACK, width=700, height=350)
        self.curtain_items = ft.Row(scroll=ft.ScrollMode.AUTO)
        self.file_picker = ft.FilePicker(on_result=self._add_files)
        self.file_button = ft.ElevatedButton(
            "Seleccionar video", icon=ft.icons.UPLOAD_FILE, on_click=self._pick_files
        )
        self.stream_button = ft.TextButton(
            text="Iniciar transmisión", width=400, on_click=self._toggle_stream
        )
        self.curtain_button = ft.TextButton(
            text="Iniciar cortina", width=400, on_click=self._toggle_curtain
        )
        return ft.Column(
            [
                ft.Row([self.preview_container, self.live_container]),
                ft.Container(bgcolor=ft.colors.BLACK, height=60, content=self.curtain_items),
                ft.ResponsiveRow(
                    [
                        self._card(
                            "Escenas", [self.file_button, self.curtain_switch, self.custom_server]
                        ),
                        self._card(
                            "Configuración de transmisión",
                            [
                                ft.Row(
                                    [
                                        ft.Column(list(self.platform_checks.values())),
                                        ft.Column(list(self.platform_keys.values())),
                                    ]
                                )
                            ],
                        ),
                        self._card("Audio y Video", [self.camera_select, self.audio_select]),
                        self._card("Controles", [self.stream_button, self.curtain_button]),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ]
        )

    @staticmethod
    def _card(title: str, controls: list[ft.Control]) -> ft.Card:
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.ListTile(title=ft.Text(title), bgcolor=ft.colors.BLACK38),
                        ft.Column(controls),
                    ]
                ),
                border_radius=10,
            ),
            col={"sm": 6, "md": 4, "xl": 3},
        )

    @staticmethod
    def _video_player(show_controls: bool = True) -> ft.Video:
        return ft.Video(
            expand=True,
            playlist=[],
            playlist_mode=ft.PlaylistMode.LOOP,
            fill_color=ft.colors.BLACK26,
            aspect_ratio=16 / 9,
            volume=100,
            autoplay=True,
            filter_quality=ft.FilterQuality.HIGH,
            show_controls=show_controls,
        )

    def did_mount(self) -> None:
        self.router.set_prevent_close(True)
        self.router.set_on_close(self._window_event)
        self.router.set_overlay(self.file_picker)
        set_environment_paths()
        try:
            video_devices, audio_devices = list_devices()
            self.camera_select.options = [
                ft.dropdown.Option(str(index), device) for index, device in enumerate(video_devices)
            ]
            self.camera_select.options.append(ft.dropdown.Option(BLACKMAGIC_DEVICE, "Blackmagic"))
            self.audio_select.options = [ft.dropdown.Option(device) for device in audio_devices]
        except (OSError, ValueError) as error:
            log.warning("No se pudo consultar la lista de dispositivos: %s", type(error).__name__)
            self._show_error(
                "No se pudieron listar los dispositivos. Compruebe que FFmpeg está instalado."
            )
        self.session.start_preview()
        self._refresh_task = self.router.page.run_task(self._refresh_preview)
        self.update()

    def will_unmount(self) -> None:
        self._close_session()
        if self.file_picker in self.router.page.overlay:
            self.router.page.overlay.remove(self.file_picker)

    def _change_platform(self, event: ft.ControlEvent) -> None:
        name = event.control.label
        self.platform_keys[name].disabled = not event.control.value
        if name == "Otro":
            self.custom_server.disabled = not event.control.value
        self.update()

    def _change_camera(self, event: ft.ControlEvent) -> None:
        self.session.select_camera(event.control.value)

    def _settings(self) -> StreamSettings:
        return StreamSettings(
            audio_device=self.audio_select.value or "",
            destinations=tuple(
                (name, self.platform_keys[name].value or "")
                for name, checkbox in self.platform_checks.items()
                if checkbox.value
            ),
            custom_server=self.custom_server.value or "",
            use_curtains=bool(self.curtain_switch.value),
        )

    def _toggle_stream(self, event: ft.ControlEvent) -> None:
        if not self._stream_lock.acquire(blocking=False):
            return
        try:
            self.stream_button.disabled = True
            self.update()
            if self._streaming:
                self.session.stop_stream()
            else:
                settings = self._settings()
                self.session.start_stream(settings)
                self.repository.save_keys(dict(settings.destinations))
        except ValueError as error:
            self._show_error(str(error))
        except Exception:
            self.session.stop_stream()
            self._show_error(
                "No se pudo iniciar o detener la transmisión. Revise la configuración."
            )
        finally:
            self._streaming = self.session.is_streaming
            self.stream_button.disabled = False
            try:
                self._update_stream_controls()
            finally:
                self._stream_lock.release()

    def _update_stream_controls(self) -> None:
        self.stream_button.text = (
            "Detener transmisión" if self._streaming else "Iniciar transmisión"
        )
        for control in (
            self.camera_select,
            self.audio_select,
            self.curtain_switch,
            self.file_button,
            *self.platform_checks.values(),
        ):
            control.disabled = self._streaming
        for name, field in self.platform_keys.items():
            field.disabled = self._streaming or not self.platform_checks[name].value
        self.custom_server.disabled = self._streaming or not self.platform_checks["Otro"].value
        self.update()

    def _toggle_curtain(self, event: ft.ControlEvent) -> None:
        try:
            active = self.session.toggle_curtain()
        except ValueError as error:
            self._show_error(str(error))
            return
        self.curtain_button.text = "Detener cortina" if active else "Iniciar cortina"
        self.live_container.content = self.live_video if active else self.live_image
        self.preview_container.content = self.preview_image if active else self.preview_video
        self.update()
        if active:
            self._load_video(self.live_video, self._selected_curtain)

    def _pick_files(self, event: ft.ControlEvent) -> None:
        self.file_picker.pick_files(allow_multiple=True, allowed_extensions=["mp4", "avi", "webm"])

    def _add_files(self, event: ft.FilePickerResultEvent) -> None:
        for selected in event.files or []:
            if not selected.path:
                continue
            capture = cv2.VideoCapture(selected.path)
            try:
                success, frame = capture.read()
                if not success:
                    self._show_error(f"No se pudo leer el video: {Path(selected.path).name}")
                    continue
                thumbnail = encode_preview(cv2.resize(frame, (200, 50)))
            finally:
                capture.release()
            self.curtain_items.controls.append(
                ft.GestureDetector(
                    content=ft.Image(src_base64=thumbnail, width=200, height=50, border_radius=20),
                    key=selected.path,
                    on_tap=self._select_curtain,
                )
            )
        self.update()

    def _select_curtain(self, event: ft.ControlEvent) -> None:
        try:
            self.session.select_curtain(event.control.key)
        except ValueError as error:
            self._show_error(str(error))
            return
        self._selected_curtain = event.control.key
        self.live_container.content = self.live_image
        self.preview_container.content = self.preview_video
        self.curtain_button.text = "Iniciar cortina"
        self.update()
        self._load_video(self.preview_video, self._selected_curtain)

    @staticmethod
    def _load_video(player: ft.Video, path: str) -> None:
        if player.playlist:
            player.playlist_remove(0)
        player.playlist_add(ft.VideoMedia(path))
        player.jump_to(0)

    async def _refresh_preview(self) -> None:
        while not self._closed:
            try:
                frame, curtain_active = self.session.preview_frames.get_nowait()
            except queue.Empty:
                pass
            else:
                image = self.preview_image if curtain_active else self.live_image
                image.src_base64 = encode_preview(frame)
                # The preview image is mounted only while the curtain is on air.
                if image.page is not None:
                    image.update()
            if self._streaming and not self.session.is_streaming and not self._stream_lock.locked():
                self._streaming = False
                self._update_stream_controls()
            try:
                error = self.session.errors.get_nowait()
            except queue.Empty:
                pass
            else:
                self._show_error(error)
            await asyncio.sleep(1 / 30)

    def _show_error(self, message: str) -> None:
        self.router.set_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Error"),
                content=ft.Text(message),
                actions=[ft.TextButton("Cerrar", on_click=lambda _: self.router.close_dialog())],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    def _window_event(self, event: ft.ControlEvent) -> None:
        if event.data == "close":
            self.router.set_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Cerrando"),
                    content=ft.Text("¿Está seguro que desea cerrar la aplicación?"),
                    actions=[
                        ft.ElevatedButton("Sí", on_click=self._confirm_close),
                        ft.OutlinedButton("No", on_click=lambda _: self.router.close_dialog()),
                    ],
                    actions_alignment=ft.MainAxisAlignment.END,
                )
            )

    def _close_session(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.session.close()

    def _confirm_close(self, event: ft.ControlEvent) -> None:
        self._close_session()
        self.router.destroy()


def HomeView(page: Router) -> HomeScreen:
    """Route factory retained for the existing navigation API."""
    return HomeScreen(page)
