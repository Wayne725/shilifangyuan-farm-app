"""Persist the category of administrator-recorded counter sales."""
from alembic import op
import sqlalchemy as sa

revision = "0051_walk_in_sale_category"
down_revision = "0050_meal_workday_policy"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("walk_in_sales") as batch:
        batch.add_column(sa.Column("sale_category", sa.String(16), nullable=True))
        batch.create_check_constraint("sale_category_valid", "sale_category IN ('meal', 'product')")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM walk_in_sales WHERE sale_category IS NOT NULL")).scalar():
        raise RuntimeError("已有現場交易分類，為保留收款紀錄不可直接降版")
    with op.batch_alter_table("walk_in_sales") as batch:
        batch.drop_constraint("sale_category_valid", type_="check")
        batch.drop_column("sale_category")
