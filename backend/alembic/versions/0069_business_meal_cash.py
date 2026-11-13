"""Allow cash collection on business meal orders."""

from alembic import op
import sqlalchemy as sa


revision = "0069_business_meal_cash"
down_revision = "0068_counter_member_phone"
branch_labels = None
depends_on = None


def _replace_contract(payment: str) -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("business_meal_contract", type_="check")
        batch.create_check_constraint(
            "business_meal_contract",
            "meal_booking_type != 'business' OR (sales_channel = 'meal_preorder' "
            f"AND {payment} AND invoice_buyer_type = 'company' "
            "AND invoice_handling IS NOT NULL AND invoice_handling = 'pickup_day' AND invoice_issue_after IS NOT NULL "
            "AND invoice_buyer_tax_id IS NOT NULL AND invoice_buyer_name IS NOT NULL)",
        )


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    _replace_contract("payment_method IN ('online', 'cash_on_pickup')")


def downgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE orders IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE orders SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM orders WHERE meal_booking_type = 'business' AND payment_method = 'cash_on_pickup')"
    )):
        raise RuntimeError("已有現金會餐訂單，請保留收款與發票紀錄並向前修復")
    _replace_contract("payment_method = 'online'")
