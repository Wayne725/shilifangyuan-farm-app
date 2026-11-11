"""Preserve the storefront selected when a payment attempt starts."""
from alembic import op
import sqlalchemy as sa

revision = "0056_payment_return_storefront"
down_revision = "0055_counter_member_pricing"
branch_labels = None
depends_on = None


def upgrade():
    is_postgresql = op.get_context().dialect.name == "postgresql"
    if is_postgresql:
        op.execute("SET LOCAL lock_timeout = '3s'")
    with op.batch_alter_table("payment_attempts") as batch:
        batch.add_column(sa.Column("browser_return_storefront", sa.String(16), nullable=True))
        # Existing rows are NULL. Enforce new writes without scanning the table under the DDL lock.
        batch.create_check_constraint(
            "browser_return_storefront_supported",
            "browser_return_storefront IN ('canonical', 'legacy')",
            postgresql_not_valid=is_postgresql,
        )


def downgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE payment_attempts IN ACCESS EXCLUSIVE MODE")
    legacy_return = op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM payment_attempts WHERE browser_return_storefront = 'legacy')"
    ))
    if legacy_return:
        raise RuntimeError("已有舊網址付款回傳紀錄，請保留交易紀錄並向前修復")
    with op.batch_alter_table("payment_attempts") as batch:
        batch.drop_constraint("browser_return_storefront_supported", type_="check")
        batch.drop_column("browser_return_storefront")
