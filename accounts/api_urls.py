from rest_framework.routers import DefaultRouter

from . import api_views

router = DefaultRouter()

router.register(
    "customers",
    api_views.CustomerViewSet,
    basename="api-customer",
)
router.register(
    "accounts",
    api_views.BankAccountViewSet,
    basename="api-bankaccount",
)
router.register(
    "transactions",
    api_views.TransactionViewSet,
    basename="api-transaction",
)
router.register(
    "standing-orders",
    api_views.StandingOrderViewSet,
    basename="api-standingorder",
)
router.register(
    "loans",
    api_views.LoanViewSet,
    basename="api-loan",
)

urlpatterns = router.urls
