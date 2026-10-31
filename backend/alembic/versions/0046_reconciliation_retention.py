"""Index only: bounded retention of completed reconciliation telemetry."""
from alembic import op
import sqlalchemy as sa

revision = "0046_reconciliation_retention"
down_revision = "0045_reconciliation_runs"
branch_labels = None
depends_on = None


def upgrade():
    predicate = sa.text("finished_at IS NOT NULL AND status IN ('succeeded','partial_failure','cancelled')")
    op.create_index("ix_reconciliation_runs_finished_id", "reconciliation_runs", ["finished_at", "id"],
                    sqlite_where=predicate, postgresql_where=predicate)


def downgrade():
    op.drop_index("ix_reconciliation_runs_finished_id", table_name="reconciliation_runs")
