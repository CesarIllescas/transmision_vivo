"""Small navigation adapter shared by Flet views."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import flet as ft


class DataStrategyEnum(Enum):
    """Legacy strategy names retained for existing view imports."""

    QUERY = 0
    ROUTER_DATA = 1
    CLIENT_STORAGE = 2
    STATE = 3


class Router:
    """Own a page's route content, transient queries, and shared view data."""

    def __init__(
        self,
        page: ft.Page,
        data_strategy: DataStrategyEnum = DataStrategyEnum.QUERY,
    ) -> None:
        self.page = page
        self.data_strategy = data_strategy
        self.data: dict[str, Any] = {}
        self.routes: dict[str, Callable[[Router], ft.Control]] = {}
        self.body = ft.Container()
        self.dialog: ft.AlertDialog | None = None
        self._query_data: dict[str, str] = {}

    def set_route(self, path: str, view: Callable[[Router], ft.Control]) -> None:
        self.routes[path] = view

    def set_routes(self, routes: Mapping[str, Callable[[Router], ft.Control]]) -> None:
        """Register or replace multiple view factories."""
        self.routes.update(routes)

    def route_change(self, event: ft.RouteChangeEvent) -> None:
        """Render the requested route and decode its standard URL query."""
        location = urlsplit(event.route)
        path = location.path or "/"
        try:
            view = self.routes[path]
        except KeyError as error:
            raise ValueError(f"No view registered for route: {path}") from error

        self._query_data = dict(parse_qsl(location.query, keep_blank_values=True))
        self.body.content = view(self)
        self.body.update()

    def set_data(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get_data(self, key: str) -> Any:
        """Read explicit view data, falling back to the current route query."""
        return self.data.get(key, self._query_data.get(key))

    def get_query(self, key: str) -> str | None:
        return self._query_data.get(key)

    def update(self) -> None:
        """Refresh mounted controls without adding another route container."""
        self.page.update()

    async def update_async(self) -> None:
        """Compatibility wrapper for asynchronous view handlers."""
        self.update()

    def set_overlay(self, overlay: ft.Control) -> None:
        if overlay not in self.page.overlay:
            self.page.overlay.append(overlay)
        self.page.update()

    def set_dialog(self, dialog: ft.AlertDialog) -> None:
        self.dialog = dialog
        self.dialog.open = True
        self.page.dialog = dialog
        self.page.update()

    def close_dialog(self) -> None:
        if self.dialog is not None:
            self.dialog.open = False
            self.page.update()

    def destroy(self) -> None:
        self.page.window.destroy()

    def set_prevent_close(self, value: bool) -> None:
        self.page.window.prevent_close = value

    def set_on_close(self, callback: Callable[[Any], None]) -> None:
        self.page.window.on_event = callback
