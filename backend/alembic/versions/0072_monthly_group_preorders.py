"""Keep existing contracts while moving active campaigns to monthly preorders."""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0072_monthly_group_preorders"
down_revision = "0071_business_pickup_locations"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "ck_group_campaigns_minimum_within_supply_cap"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    inspector = sa.inspect(bind)
    if "preorder_only" not in {column["name"] for column in inspector.get_columns("group_campaigns")}:
        op.add_column("group_campaigns", sa.Column("preorder_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    constraint = next(item for item in inspector.get_check_constraints("group_campaigns") if item["name"] == CONSTRAINT_NAME)
    if "preorder_only" not in constraint["sqltext"]:
        with op.batch_alter_table("group_campaigns") as batch:
            batch.drop_constraint(op.f(CONSTRAINT_NAME), type_="check")
            batch.create_check_constraint(op.f(CONSTRAINT_NAME), "preorder_only OR min_paid_quantity <= supply_cap")
    campaigns = sa.table("group_campaigns", sa.column("decision_status", sa.String()),
        sa.column("preorder_only", sa.Boolean()), sa.column("legacy_confirmed_intake", sa.Boolean()),
        sa.column("estimated_pickup_end", sa.DateTime(timezone=True)), sa.column("final_pickup_at", sa.DateTime(timezone=True)))
    now = datetime.now(timezone.utc)
    active = sa.or_(campaigns.c.decision_status.in_(["recruiting", "pending_confirmation"]),
        sa.and_(campaigns.c.decision_status == "confirmed", sa.or_(
            campaigns.c.final_pickup_at >= now,
            sa.and_(campaigns.c.final_pickup_at.is_(None), campaigns.c.estimated_pickup_end >= now))))
    # The legacy flag lets workers from the previous release accept callbacks during deployment.
    bind.execute(campaigns.update().where(active).values(
        preorder_only=True, decision_status="confirmed", legacy_confirmed_intake=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE group_campaigns IN ACCESS EXCLUSIVE MODE")
    else:
        op.execute("UPDATE group_campaigns SET id = id WHERE 0")
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM group_campaigns WHERE min_paid_quantity > supply_cap)")):
        raise RuntimeError("預訂檔期的舊門檻已高於供應上限，無法恢復舊約束，請向前修復")
    # Keep confirmed commitments and legacy callback compatibility when removing the new policy flag.
    with op.batch_alter_table("group_campaigns") as batch:
        batch.drop_constraint(op.f(CONSTRAINT_NAME), type_="check")
        batch.create_check_constraint(op.f(CONSTRAINT_NAME), "min_paid_quantity <= supply_cap")
        batch.drop_column("preorder_only")
