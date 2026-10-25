"""Version unpaid order quantity changes and their payment attempts."""
from alembic import op
import sqlalchemy as sa

revision = "0035_order_amendments"
down_revision = "0034_meal_pickup_utensils"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("payment_attempts", sa.Column("order_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("payment_attempts", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("refunds", sa.Column("purpose", sa.String(40), nullable=False, server_default="order_cancellation"))
    op.create_table("order_amendments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("client_request_id", sa.String(36), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("previous_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False),
        sa.Column("after_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("order_id", "client_request_id", name="uq_order_amendments_client_request"),
        sa.UniqueConstraint("order_id", "revision", name="uq_order_amendments_revision"),
        sa.CheckConstraint("revision = previous_revision + 1", name="revision_increment"),
    )
    op.create_index("ix_order_amendments_order_id", "order_amendments", ["order_id"])
    op.create_index("ix_order_amendments_actor_id", "order_amendments", ["actor_id"])
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_constraint(op.f("ck_print_jobs_valid_kind"), type_="check")
        batch.create_check_constraint(op.f("ck_print_jobs_valid_kind"), "kind IN ('order','cancellation','amendment')")


def downgrade():
    connection = op.get_bind()
    guards = [
        "SELECT COUNT(*) FROM order_amendments",
        "SELECT COUNT(*) FROM orders WHERE revision <> 1",
        "SELECT COUNT(*) FROM payment_attempts WHERE order_revision <> 1 OR superseded_at IS NOT NULL",
        "SELECT COUNT(*) FROM refunds WHERE purpose <> 'order_cancellation'",
        "SELECT COUNT(*) FROM print_jobs WHERE kind = 'amendment'",
    ]
    if any(connection.scalar(sa.text(query)) for query in guards):
        raise RuntimeError("已有訂單數量修訂或舊版付款紀錄，無法安全降版")
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_constraint(op.f("ck_print_jobs_valid_kind"), type_="check")
        batch.create_check_constraint(op.f("ck_print_jobs_valid_kind"), "kind IN ('order','cancellation')")
    op.drop_table("order_amendments")
    with op.batch_alter_table("refunds") as batch:
        batch.drop_column("purpose")
    with op.batch_alter_table("payment_attempts") as batch:
        batch.drop_column("superseded_at")
        batch.drop_column("order_revision")
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("revision")
