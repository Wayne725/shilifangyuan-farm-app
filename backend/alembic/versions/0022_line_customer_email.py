"""Allow LINE customers to supply notification email at checkout."""

from alembic import op
import sqlalchemy as sa

revision = "0022_line_customer_email"
down_revision = "0021_line_login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email", existing_type=sa.String(320), nullable=True)
        batch.add_column(sa.Column("notification_email", sa.String(320), nullable=True))


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM users WHERE email IS NULL")).scalar_one():
        raise RuntimeError("已有未填Email的LINE顧客，請先安排帳號保留方式，不能直接回復必填Email")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("notification_email")
        batch.alter_column("email", existing_type=sa.String(320), nullable=False)
