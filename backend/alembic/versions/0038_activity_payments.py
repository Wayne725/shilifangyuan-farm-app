"""Add independent activity receivables without charging historical registrations."""
from alembic import op
import sqlalchemy as sa


revision = "0038_activity_payments"
down_revision = "0037_group_review_window"
branch_labels = None
depends_on = None

REGISTRATION_STATUSES = ("registered", "waitlisted", "cancelled", "attended", "no_show")
PAYMENT_STATUSES = ("pending", "paid", "late_paid_refund_required", "refund_pending", "refunded", "failed", "expired")
OLD_SUBJECT_CHECK = (
    "(order_id IS NOT NULL AND membership_charge_id IS NULL) OR "
    "(order_id IS NULL AND membership_charge_id IS NOT NULL)"
)
SUBJECT_CHECK = (
    "(order_id IS NOT NULL AND membership_charge_id IS NULL AND activity_charge_id IS NULL) OR "
    "(order_id IS NULL AND membership_charge_id IS NOT NULL AND activity_charge_id IS NULL) OR "
    "(order_id IS NULL AND membership_charge_id IS NULL AND activity_charge_id IS NOT NULL)"
)


def upgrade():
    with op.batch_alter_table("activities") as batch:
        batch.add_column(sa.Column("member_price", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("non_member_price", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("payment_window_hours", sa.Integer(), nullable=False, server_default="24"))
        batch.create_check_constraint("activity_prices_nonnegative", "member_price >= 0 AND non_member_price >= 0")
        batch.create_check_constraint("activity_payment_window_valid", "payment_window_hours BETWEEN 1 AND 168")
    with op.batch_alter_table("activity_registrations") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("contact_name", sa.String(160), nullable=False, server_default=""))
        batch.add_column(sa.Column("contact_email", sa.String(320), nullable=True))
        batch.create_check_constraint("registration_revision_positive", "revision >= 1")
        batch.alter_column("status", existing_type=sa.String(10),
                           type_=sa.Enum(*REGISTRATION_STATUSES, "pending_payment", name="activity_registration_status", native_enum=False),
                           existing_nullable=False)

    op.create_table(
        "activity_charges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("activity_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("registration_id", sa.String(36), nullable=False),
        sa.Column("registration_revision", sa.Integer(), nullable=False),
        sa.Column("activity_title", sa.String(160), nullable=False),
        sa.Column("contact_name", sa.String(160), nullable=False),
        sa.Column("contact_email", sa.String(320), nullable=True),
        sa.Column("membership_type", sa.Enum("member", "trainee", "nonmember", name="activity_charge_membership_type", native_enum=False), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("payment_status", sa.Enum(*PAYMENT_STATUSES, name="activity_charge_payment_status", native_enum=False), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["registration_id"], ["activity_registrations.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("number"),
        sa.UniqueConstraint("registration_id", "registration_revision", name="uq_activity_charge_registration_revision"),
        sa.CheckConstraint("registration_revision >= 1", name="activity_charge_revision_positive"),
        sa.CheckConstraint("amount > 0", name="activity_charge_amount_positive"),
    )
    for column in ("activity_id", "user_id", "registration_id"):
        op.create_index(f"ix_activity_charges_{column}", "activity_charges", [column])

    for table, check_name in (("payment_attempts", "exactly_one_payment_subject"), ("refunds", "exactly_one_refund_subject")):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("activity_charge_id", sa.String(36), nullable=True))
            batch.create_foreign_key(op.f(f"fk_{table}_activity_charge_id_activity_charges"), "activity_charges", ["activity_charge_id"], ["id"], ondelete="RESTRICT")
            batch.create_index(f"ix_{table}_activity_charge_id", ["activity_charge_id"])
            batch.drop_constraint(op.f(f"ck_{table}_{check_name}"), type_="check")
            batch.create_check_constraint(check_name, SUBJECT_CHECK)

    op.create_table(
        "activity_refund_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("activity_charge_id", sa.String(36), nullable=False),
        sa.Column("payment_attempt_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_review"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("requested_by_id", sa.String(36), nullable=True),
        sa.Column("reviewed_by_id", sa.String(36), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["activity_charge_id"], ["activity_charges.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_attempt_id"], ["payment_attempts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("payment_attempt_id"),
        sa.CheckConstraint("amount > 0", name="activity_refund_request_amount_positive"),
        sa.CheckConstraint("version >= 1", name="activity_refund_request_version_positive"),
        sa.CheckConstraint("status IN ('pending_review', 'approved', 'rejected', 'externally_refunded')", name="activity_refund_request_status_valid"),
    )
    op.create_index("ix_activity_refund_requests_activity_charge_id", "activity_refund_requests", ["activity_charge_id"])


def downgrade():
    bind = op.get_bind()
    incompatible_queries = (
        "SELECT COUNT(*) FROM activity_charges",
        "SELECT COUNT(*) FROM activity_refund_requests",
        "SELECT COUNT(*) FROM payment_attempts WHERE activity_charge_id IS NOT NULL",
        "SELECT COUNT(*) FROM refunds WHERE activity_charge_id IS NOT NULL",
        "SELECT COUNT(*) FROM activities WHERE member_price <> 0 OR non_member_price <> 0 OR payment_window_hours <> 24",
        "SELECT COUNT(*) FROM activity_registrations WHERE revision <> 1 OR status = 'pending_payment' OR contact_name <> '' OR contact_email IS NOT NULL",
    )
    if any(bind.scalar(sa.text(query)) for query in incompatible_queries):
        raise RuntimeError("活動已有收費設定、報名快照或款項紀錄，無法安全降版；請以升版前備份規劃還原")

    op.drop_table("activity_refund_requests")
    for table, check_name in (("refunds", "exactly_one_refund_subject"), ("payment_attempts", "exactly_one_payment_subject")):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(f"ck_{table}_{check_name}"), type_="check")
            batch.create_check_constraint(check_name, OLD_SUBJECT_CHECK)
            batch.drop_index(f"ix_{table}_activity_charge_id")
            batch.drop_constraint(op.f(f"fk_{table}_activity_charge_id_activity_charges"), type_="foreignkey")
            batch.drop_column("activity_charge_id")
    op.drop_table("activity_charges")
    with op.batch_alter_table("activity_registrations") as batch:
        batch.drop_constraint(op.f("ck_activity_registrations_registration_revision_positive"), type_="check")
        for column in ("contact_email", "contact_name", "revision"):
            batch.drop_column(column)
        batch.alter_column("status", existing_type=sa.String(15),
                           type_=sa.Enum(*REGISTRATION_STATUSES, name="activity_registration_status", native_enum=False), existing_nullable=False)
    with op.batch_alter_table("activities") as batch:
        for constraint in ("activity_prices_nonnegative", "activity_payment_window_valid"):
            batch.drop_constraint(op.f(f"ck_activities_{constraint}"), type_="check")
        for column in ("payment_window_hours", "non_member_price", "member_price"):
            batch.drop_column(column)
