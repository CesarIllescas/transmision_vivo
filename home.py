import flet as ft

def main(page: ft.Page):
    page.title = "home"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER

    txt = ft.TextField(value="Hello world!", text_align=ft.TextAlign.RIGHT, width=120)

    page.add(
        ft.Row(
            [
                txt,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        )
    )

ft.app(target=main)