"""Route registration for each application page."""

import flet as ft

from pages.Router import Router


def create_router(page: ft.Page) -> Router:
    """Build a router only after Flet provides the actual page."""
    from pages.home_view import HomeView

    router = Router(page)
    router.set_route("/", HomeView)
    return router
