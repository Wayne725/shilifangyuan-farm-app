from .auth import auth_router
from .catalog import catalog_router
from .groups import groups_router
from .invoices import invoices_router
from .notifications import notifications_router
from .orders import orders_router
from .payments import payments_router
from .proposals import proposals_router

ALL_ROUTERS = [
    auth_router,
    catalog_router,
    proposals_router,
    groups_router,
    orders_router,
    payments_router,
    invoices_router,
    notifications_router,
]

__all__ = [
    "ALL_ROUTERS",
    "auth_router",
    "catalog_router",
    "groups_router",
    "invoices_router",
    "notifications_router",
    "orders_router",
    "payments_router",
    "proposals_router",
]
