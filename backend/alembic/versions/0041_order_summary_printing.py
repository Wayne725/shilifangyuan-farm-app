"""Allow durable summary print jobs without a fabricated order."""
from alembic import op
import sqlalchemy as sa

revision = "0041_order_summary_printing"
down_revision = "0040_membership_admission"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_constraint(op.f("ck_print_jobs_valid_kind"), type_="check")
        batch.alter_column("order_id", existing_type=sa.String(36), nullable=True)
        batch.create_check_constraint(op.f("ck_print_jobs_valid_kind"), "kind IN ('order','cancellation','amendment','summary')")
        batch.create_check_constraint(op.f("ck_print_jobs_valid_order_reference"),
            "(kind = 'summary' AND order_id IS NULL) OR (kind <> 'summary' AND order_id IS NOT NULL)")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM print_jobs WHERE kind = 'summary'")):
        raise RuntimeError("已有訂單彙總出單紀錄，請保留紀錄並向前修復")
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_constraint(op.f("ck_print_jobs_valid_order_reference"), type_="check")
        batch.drop_constraint(op.f("ck_print_jobs_valid_kind"), type_="check")
        batch.alter_column("order_id", existing_type=sa.String(36), nullable=False)
        batch.create_check_constraint(op.f("ck_print_jobs_valid_kind"), "kind IN ('order','cancellation','amendment')")
