"""Keep business meal booking intent on each order."""
from alembic import op
import sqlalchemy as sa

revision = '0052_business_meal_orders'
down_revision = '0051_walk_in_sale_category'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orders') as batch:
        batch.add_column(sa.Column('meal_booking_type', sa.String(16), nullable=False, server_default='standard'))
        batch.create_check_constraint('meal_booking_type_valid', "meal_booking_type IN ('standard', 'business')")
        batch.create_check_constraint('business_meal_contract', "meal_booking_type != 'business' OR (sales_channel = 'meal_preorder' AND payment_method = 'online' AND invoice_buyer_type = 'company' AND invoice_handling IS NOT NULL AND invoice_handling = 'pickup_day' AND invoice_issue_after IS NOT NULL AND invoice_buyer_tax_id IS NOT NULL AND invoice_buyer_name IS NOT NULL)")
        batch.create_index('ix_orders_meal_booking_type', ['meal_booking_type'])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM orders WHERE meal_booking_type = 'business'")).scalar():
        raise RuntimeError('已有會餐訂單，為保留付款與發票安排不可直接降版')
    with op.batch_alter_table('orders') as batch:
        batch.drop_index('ix_orders_meal_booking_type')
        batch.drop_constraint('business_meal_contract', type_='check')
        batch.drop_constraint('meal_booking_type_valid', type_='check')
        batch.drop_column('meal_booking_type')
