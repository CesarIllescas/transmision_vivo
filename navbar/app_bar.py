import flet as ft

def AppBar(page):

    AppBar = ft.MenuBar(
      style=ft.MenuStyle(
        alignment=ft.alignment.top_left,
        mouse_cursor={ft.MaterialState.HOVERED: ft.MouseCursor.WAIT,
                       ft.MaterialState.DEFAULT: ft.MouseCursor.ZOOM_OUT},
      ),
      controls=[
        ft.SubmenuButton(
          content=ft.Text("Archivo"),
        ),
        ft.SubmenuButton(
          content=ft.Text("Editar"),
        ),
        ft.SubmenuButton(
          content=ft.Text("Vista"),
        ),
        ft.SubmenuButton(
          content=ft.Text("Perfil"),
        ),
        ft.SubmenuButton(
          content=ft.Text("Herramientas"),
          controls=[
            
          ]
        ),
      ]
    )

    return AppBar