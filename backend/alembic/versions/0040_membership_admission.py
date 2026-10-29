"""Versioned admission education and review, without membership or billing."""
from alembic import op
import sqlalchemy as sa


revision = "0040_membership_admission"
down_revision = "0039_product_daily_sales"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "membership_admission_rounds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("flow_version", sa.String(40), nullable=False, server_default="education-review-v1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("profile_encrypted", sa.Text(), nullable=True),
        sa.Column("requested_share_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("student_claimed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("discovery_channel", sa.String(100), nullable=True),
        sa.Column("discovery_other_encrypted", sa.Text(), nullable=True),
        sa.Column("declaration_version", sa.String(64), nullable=True),
        sa.Column("privacy_version", sa.String(64), nullable=True),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("material_version", sa.String(80), nullable=True),
        sa.Column("material_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quiz_version", sa.String(64), nullable=False, server_default="2026-10-29.v1"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("decision_reference", sa.String(240), nullable=True),
        sa.Column("student_verified", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "round_number"),
        sa.UniqueConstraint("user_id", "idempotency_key"),
        sa.CheckConstraint("round_number >= 1 AND version >= 1", name="round_version_positive"),
        sa.CheckConstraint("status IN ('draft','submitted','needs_supplement','approved','rejected','withdrawn')", name="status_valid"),
        sa.CheckConstraint("requested_share_count BETWEEN 0 AND 100", name="requested_shares_valid"),
    )
    for column in ("user_id", "status", "created_at"):
        op.create_index(f"ix_membership_admission_rounds_{column}", "membership_admission_rounds", [column])
    op.create_table(
        "membership_admission_quiz_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("round_id", sa.String(36), sa.ForeignKey("membership_admission_rounds.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("quiz_version", sa.String(64), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("settings_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_interval_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("round_id", "attempt_number"),
        sa.UniqueConstraint("round_id", "idempotency_key"),
        sa.CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name="score_valid"),
    )
    op.create_index("ix_membership_admission_quiz_attempts_round_id", "membership_admission_quiz_attempts", ["round_id"])
    op.create_table(
        "membership_admission_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("round_id", sa.String(36), sa.ForeignKey("membership_admission_rounds.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("profile_encrypted", sa.Text(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("quiz_attempt_id", sa.String(36), sa.ForeignKey("membership_admission_quiz_attempts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("round_id", "revision_number"),
        sa.CheckConstraint("revision_number >= 1", name="revision_positive"),
    )
    op.create_index("ix_membership_admission_submissions_round_id", "membership_admission_submissions", ["round_id"])
    op.create_table(
        "membership_admission_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("round_id", sa.String(36), sa.ForeignKey("membership_admission_rounds.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submission_id", sa.String(36), sa.ForeignKey("membership_admission_submissions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decision_reference", sa.String(240), nullable=True),
        sa.Column("student_verified", sa.Boolean(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("submission_id"),
        sa.CheckConstraint("decision IN ('approve','request_supplement','reject')", name="decision_valid"),
    )
    op.create_index("ix_membership_admission_reviews_round_id", "membership_admission_reviews", ["round_id"])


def downgrade():
    tables = ("membership_admission_reviews", "membership_admission_submissions", "membership_admission_quiz_attempts", "membership_admission_rounds")
    if any(op.get_bind().scalar(sa.text(f"SELECT COUNT(*) FROM {table}")) for table in tables):
        raise RuntimeError("已有入社申請或測驗紀錄，降版會刪除申請歷程；請先完成資料保全")
    for table in tables:
        op.drop_table(table)
