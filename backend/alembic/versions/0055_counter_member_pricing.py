"""Record verified counter membership without assigning a website order owner."""
from alembic import op
import sqlalchemy as sa

revision = "0055_counter_member_pricing"
down_revision = "0054_catalog_visibility"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("verified_counter_membership_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_orders_verified_counter_membership_id_memberships", "memberships",
            ["verified_counter_membership_id"], ["id"], ondelete="RESTRICT",
        )
        batch.create_index("ix_orders_verified_counter_membership_id", ["verified_counter_membership_id"])
        batch.drop_constraint("customer_identity", type_="check")
        batch.create_check_constraint(
            "customer_identity",
            "(customer_kind = 'account' AND user_id IS NOT NULL AND verified_counter_membership_id IS NULL) OR "
            "(customer_kind = 'walk_in' AND user_id IS NULL AND sales_channel = 'regular' AND payment_method = 'cash_on_pickup' AND "
            "((verified_counter_membership_id IS NULL AND membership_type_snapshot = 'nonmember' AND NOT member_tax_exempt_snapshot) OR "
            "(verified_counter_membership_id IS NOT NULL AND membership_type_snapshot IN ('member', 'trainee') AND "
            "member_tax_exempt_snapshot = (membership_type_snapshot = 'member' AND invoice_buyer_type = 'personal'))))",
        )
    op.create_table(
        "walk_in_request_guards",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("retired", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM orders WHERE verified_counter_membership_id IS NOT NULL")):
        raise RuntimeError("已有社員現場交易，請保留會籍與收款紀錄並向前修復")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM walk_in_request_guards WHERE retired = true")):
        raise RuntimeError("已有作廢的現場操作編號，請保留交易防重資料並向前修復")
    op.drop_table("walk_in_request_guards")
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("customer_identity", type_="check")
        batch.drop_index("ix_orders_verified_counter_membership_id")
        batch.drop_constraint("fk_orders_verified_counter_membership_id_memberships", type_="foreignkey")
        batch.drop_column("verified_counter_membership_id")
        batch.create_check_constraint(
            "customer_identity",
            "(customer_kind = 'account' AND user_id IS NOT NULL) OR "
            "(customer_kind = 'walk_in' AND user_id IS NULL AND membership_type_snapshot = 'nonmember' "
            "AND NOT member_tax_exempt_snapshot AND sales_channel = 'regular' AND payment_method = 'cash_on_pickup')",
        )
