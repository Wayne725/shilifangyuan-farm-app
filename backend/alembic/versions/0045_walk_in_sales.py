"""Represent anonymous counter sales without assigning them to an operator."""
from alembic import op
import sqlalchemy as sa

revision = "0045_walk_in_sales"
down_revision = "0044_invoice_timing"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders") as batch:
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("customer_kind", sa.String(16), nullable=False, server_default="account"))
        batch.add_column(sa.Column("customer_name_snapshot", sa.String(120), nullable=True))
        batch.create_check_constraint("customer_identity",
            "(customer_kind = 'account' AND user_id IS NOT NULL) OR "
            "(customer_kind = 'walk_in' AND user_id IS NULL AND membership_type_snapshot = 'nonmember' "
            "AND NOT member_tax_exempt_snapshot AND sales_channel = 'regular' AND payment_method = 'cash_on_pickup')")
    op.create_table("walk_in_sales",
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("client_request_id", sa.String(36), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("operator_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_walk_in_sales_operator_id", "walk_in_sales", ["operator_id"])


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM walk_in_sales")) or bind.scalar(sa.text("SELECT count(*) FROM orders WHERE user_id IS NULL OR customer_kind <> 'account'")):
        raise RuntimeError("已有現場交易，請保留客人與收款紀錄並向前修復")
    op.drop_table("walk_in_sales")
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("customer_identity", type_="check")
        batch.drop_column("customer_name_snapshot")
        batch.drop_column("customer_kind")
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=False)
