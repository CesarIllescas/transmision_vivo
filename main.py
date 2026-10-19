"""Entry point for the desktop live streaming application."""

import flet as ft

from pages.navigation import create_router


def main(page: ft.Page) -> None:
    """Configure a page and mount its navigation container once."""
    page.theme_mode = ft.ThemeMode.DARK
    page.title = "Transmisión en Vivo"

    router = create_router(page)
    page.on_route_change = router.route_change
    page.add(router.body)
    page.go("/")


def run() -> None:
    ft.app(target=main, assets_dir="assets")


if __name__ == "__main__":
    run()
