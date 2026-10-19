"""Application menu construction."""

import flet as ft

MENU_LABELS = ("Archivo", "Editar", "Vista", "Perfil", "Herramientas")


def create_app_bar(page: ft.Page) -> ft.MenuBar:
    """Build the application menu; page is reserved for menu actions."""
    return ft.MenuBar(
        style=ft.MenuStyle(alignment=ft.alignment.top_left),
        controls=[ft.SubmenuButton(content=ft.Text(label)) for label in MENU_LABELS],
    )


# Preserve the original factory name for callers outside this project.
AppBar = create_app_bar
