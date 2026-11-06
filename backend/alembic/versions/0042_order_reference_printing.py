"""Allow clearly marked manual order records without authorizing meal preparation."""
from alembic import op
import sqlalchemy as sa

revision = "0042_order_reference_printing"
down_revision = "0041_order_summary_printing"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_constraint(op.f("ck_print_jobs_valid_kind"), type_="check")
        batch.create_check_constraint(op.f("ck_print_jobs_valid_kind"),
            "kind IN ('order','cancellation','amendment','summary','reference')")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM print_jobs WHERE kind = 'reference'")):
        raise RuntimeError("已有訂單紀錄單，請保留出單紀錄並向前修復")
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_constraint(op.f("ck_print_jobs_valid_kind"), type_="check")
        batch.create_check_constraint(op.f("ck_print_jobs_valid_kind"),
            "kind IN ('order','cancellation','amendment','summary')")
