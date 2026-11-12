"""Add product archives, roster addresses, and meeting resolution revocation."""

from alembic import op
import sqlalchemy as sa


revision = "0065_admin_editing"
down_revision = "0064_meal_operations"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    inspector = sa.inspect(op.get_bind())
    for table, column in (
        ("products", sa.Column("archived_at", sa.DateTime(timezone=True))),
        ("member_roster_entries", sa.Column("address_encrypted", sa.Text())),
        ("meeting_resolutions", sa.Column("revoked_at", sa.DateTime(timezone=True))),
    ):
        # Early revisions that create tables from live metadata may already have the column.
        if column.name not in {existing["name"] for existing in inspector.get_columns(table)}:
            op.add_column(table, column)


def downgrade():
    columns = [("products", "archived_at"), ("member_roster_entries", "address_encrypted"),
               ("meeting_resolutions", "revoked_at")]
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE products, member_roster_entries, meeting_resolutions IN ACCESS EXCLUSIVE MODE")
    else:
        op.execute("UPDATE products SET id = id WHERE 0")
    for table, column in columns:
        if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {column} IS NOT NULL)")):
            raise RuntimeError("已保存封存、名冊地址或撤銷紀錄，請向前修復")
    for table, column in reversed(columns):
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
