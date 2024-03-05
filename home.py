import flet as ft
from pages.navigation import router
from navbar.app_bar import AppBar

def main(page: ft.Page):

    page.theme_mode = "dark"
    page.title = "SICOM Studio"
    page.appbar = AppBar(page)
    page.on_route_change = router.route_change
    router.page = page
    page.add(
        router.body
    )
    page.go('/')

ft.app(target=main, assets_dir="assets")