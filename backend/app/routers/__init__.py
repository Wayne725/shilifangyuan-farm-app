from .activity_payments import router as activity_payments_router
from .activity_finance import router as activity_finance_router
from .photos import photos_router
from .auth import auth_router
from .member_lookup import member_lookup_router
from .line_auth import line_auth_router
from .admin_help import admin_help_router
from .admin_users import admin_users_router
from .catalog import catalog_router
from .community import community_router
from .cooperative import cooperative_router
from .groups import groups_router
from .invoices import invoices_router
from .logistics import logistics_router
from .meals import meals_router
from .membership import membership_router
from .membership_admission import membership_admission_router
from .membership_education_admin import membership_education_admin_router
from .notifications import notifications_router
from .orders import orders_router
from .order_summary import order_summary_router
from .operations import operations_router
from .payments import payments_router
from .platform import platform_router
from .member_reports import member_reports_router
from .meal_cancellation_reports import meal_cancellation_reports_router
from .proposals import proposals_router

from .promotions import promotions_router

from .invoice_reports import invoice_reports_router
from .financial_statements import router as financial_statements_router
from .walk_in_sales import walk_in_sales_router

ALL_ROUTERS = [
    admin_help_router,
    walk_in_sales_router,
    activity_payments_router,
    activity_finance_router,
    photos_router,
    financial_statements_router,
    invoice_reports_router,
    promotions_router,
    platform_router,
    member_reports_router,
    meal_cancellation_reports_router,
    auth_router,
    member_lookup_router,
    line_auth_router,
    admin_users_router,
    catalog_router,
    membership_router,
    membership_admission_router,
    membership_education_admin_router,
    community_router,
    cooperative_router,
    meals_router,
    proposals_router,
    groups_router,
    orders_router,
    order_summary_router,
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
    "order_summary_router",
    "operations_router",
    "payments_router",
    "proposals_router",
]
