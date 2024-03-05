from pages.Router import Router, DataStrategyEnum

from pages.home_view import HomeView

router = Router(DataStrategyEnum.QUERY)

router.routes = {
  "/": HomeView,
}