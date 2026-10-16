from .auth import auth_router
from .line_auth import line_auth_router
from .admin_users import admin_users_router
from .catalog import catalog_router
from .community import community_router
from .cooperative import cooperative_router
from .groups import groups_router
from .invoices import invoices_router
from .logistics import logistics_router
from .meals import meals_router
from .membership import membership_router
from .notifications import notifications_router
from .orders import orders_router
from .operations import operations_router
from .payments import payments_router
from .platform import platform_router
from .member_reports import member_reports_router
from .meal_cancellation_reports import meal_cancellation_reports_router
from .proposals import proposals_router

from .promotions import promotions_router

from .invoice_reports import invoice_reports_router

ALL_ROUTERS = [
    invoice_reports_router,
    promotions_router,
    platform_router,
    member_reports_router,
    meal_cancellation_reports_router,
    auth_router,
    line_auth_router,
    admin_users_router,
    catalog_router,
    membership_router,
    community_router,
    cooperative_router,
    meals_router,
    proposals_router,
    groups_router,
    orders_router,
    operations_router,
    payments_router,
    invoices_router,
    logistics_router,
    notifications_router,
]

__all__ = [
    "ALL_ROUTERS",
    "auth_router",
    "catalog_router",
    "community_router",
    "cooperative_router",
    "groups_router",
    "invoices_router",
    "logistics_router",
    "meals_router",
    "membership_router",
    "notifications_router",
    "orders_router",
    "operations_router",
    "payments_router",
    "proposals_router",
]
