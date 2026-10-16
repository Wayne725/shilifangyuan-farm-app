"""Audited offline admission fee and share capital receipts."""
from alembic import op
import sqlalchemy as sa
revision = "0026_membership_offline_receipts"
down_revision = "0025_platform_member_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("membership_offline_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(36), nullable=False, unique=True),
        sa.Column("membership_id", sa.String(36), sa.ForeignKey("memberships.id", ondelete="RESTRICT")),
        sa.Column("roster_entry_id", sa.String(36), sa.ForeignKey("member_roster_entries.id", ondelete="RESTRICT")),
        sa.Column("member_number", sa.String(32), nullable=False),
        sa.Column("charge_kind", sa.String(24), nullable=False), sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("payment_method", sa.String(24), nullable=False), sa.Column("payer_reference_encrypted", sa.Text()),
        sa.Column("receipt_number", sa.String(40), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True)), sa.Column("void_reason", sa.Text()),
        sa.CheckConstraint("(membership_id IS NULL) <> (roster_entry_id IS NULL)", name="one_member_target"),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
        sa.CheckConstraint("charge_kind IN ('admission_fee', 'share_capital')", name="charge_kind_valid"),
        sa.CheckConstraint("payment_method IN ('cash', 'bank_transfer', 'other')", name="payment_method_valid"))
    for column in ("membership_id", "roster_entry_id", "member_number", "charge_kind", "paid_at", "receipt_number"):
        op.create_index(f"ix_membership_offline_receipts_{column}", "membership_offline_receipts", [column], unique=column == "receipt_number")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM membership_offline_receipts")).scalar():
        raise RuntimeError("已有線下收款紀錄，禁止移除收款資料表")
    op.drop_table("membership_offline_receipts")
