"""Navigation regressions that run without opening a Flet window."""

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_module(relative_path):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.flet = SimpleNamespace(
            Container=lambda: SimpleNamespace(content=None, update=Mock()),
            Page=Mock(),
            ThemeMode=SimpleNamespace(DARK="dark"),
            app=Mock(),
        )
        self.flet_patch = patch.dict(sys.modules, {"flet": self.flet})
        self.flet_patch.start()
        self.addCleanup(self.flet_patch.stop)
        self.router_module = load_module("pages/Router.py")
        self.page = SimpleNamespace(overlay=[], update=Mock(), add=Mock())
        self.router = self.router_module.Router(self.page)
        self.view = Mock(return_value=object())
        self.router.set_route("/", self.view)

    def navigate(self, route):
        self.router.route_change(SimpleNamespace(route=route))

    def test_queries_decode_separators_spaces_unicode_and_blank_values(self):
        self.navigate(
            "/?name=Jos%C3%A9+P%C3%A9rez&token=a%26b%3Dc&empty=&flag&repeated=first&repeated=last"
        )

        self.assertEqual(self.router.get_query("name"), "José Pérez")
        self.assertEqual(self.router.get_query("token"), "a&b=c")
        self.assertEqual(self.router.get_query("empty"), "")
        self.assertEqual(self.router.get_query("flag"), "")
        self.assertEqual(self.router.get_query("repeated"), "last")
        self.view.assert_called_once_with(self.router)
        self.assertIs(self.router.body.content, self.view.return_value)

    def test_navigation_replaces_queries_but_preserves_explicit_data(self):
        self.router.set_data("device", "camera")
        self.navigate("/?temporary=preview")
        self.assertEqual(self.router.get_data("temporary"), "preview")

        self.navigate("/")

        self.assertIsNone(self.router.get_query("temporary"))
        self.assertIsNone(self.router.get_data("temporary"))
        self.assertEqual(self.router.get_data("device"), "camera")

    def test_refreshes_do_not_remount_the_navigation_container(self):
        self.router.update()
        self.router.update()
        asyncio.run(self.router.update_async())

        self.assertEqual(self.page.update.call_count, 3)
        self.page.add.assert_not_called()

    def test_unknown_route_keeps_existing_view(self):
        self.navigate("/")
        previous_content = self.router.body.content

        with self.assertRaisesRegex(ValueError, "No view registered"):
            self.navigate("/missing")

        self.assertIs(self.router.body.content, previous_content)

    def test_overlay_is_only_added_once(self):
        overlay = object()
        self.router.set_overlay(overlay)
        self.router.set_overlay(overlay)

        self.assertEqual(self.page.overlay, [overlay])

    def test_navigation_factory_uses_a_separate_router_for_each_page(self):
        with patch.dict(
            sys.modules,
            {
                "pages.Router": self.router_module,
                "pages.home_view": SimpleNamespace(HomeView=self.view),
            },
        ):
            navigation = load_module("pages/navigation.py")
            first = navigation.create_router(self.page)
            second_page = SimpleNamespace()
            second = navigation.create_router(second_page)

        self.assertIs(first.page, self.page)
        self.assertIs(second.page, second_page)
        self.assertIs(first.routes["/"], self.view)
        self.assertIsNot(first, second)

    def test_navigation_import_does_not_create_a_router_or_load_media(self):
        router_class = Mock()
        with patch.dict(
            sys.modules,
            {
                "pages.Router": SimpleNamespace(Router=router_class),
                "pages.home_view": None,
            },
        ):
            load_module("pages/navigation.py")

        router_class.assert_not_called()

    def test_main_import_does_not_launch_and_main_mounts_router_once(self):
        create_router = Mock(return_value=self.router)
        self.page.go = Mock()
        with patch.dict(
            sys.modules,
            {"pages.navigation": SimpleNamespace(create_router=create_router)},
        ):
            entry = load_module("main.py")
            self.flet.app.assert_not_called()
            create_router.assert_not_called()

            entry.main(self.page)

        create_router.assert_called_once_with(self.page)
        self.page.add.assert_called_once_with(self.router.body)
        self.page.go.assert_called_once_with("/")
        self.assertEqual(self.page.on_route_change, self.router.route_change)
        self.assertEqual(self.page.title, "Transmisión en Vivo")


if __name__ == "__main__":
    unittest.main()
