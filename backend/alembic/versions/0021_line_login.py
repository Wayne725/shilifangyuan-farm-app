"""Add verified LINE identities and one-time browser-bound authorization state."""

from alembic import op
import sqlalchemy as sa

revision = "0021_line_login"
down_revision = "0020_meal_booking_deadline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("registration_intent", sa.String(24), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "subject", name="uq_external_identity_subject"),
        sa.UniqueConstraint("provider", "user_id", name="uq_external_identity_user"),
    )
    op.create_index("ix_external_identities_user_id", "external_identities", ["user_id"])
    op.create_table(
        "line_oauth_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("target_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("target_token_version", sa.Integer(), nullable=True),
        sa.Column("registration_intent", sa.String(24), nullable=False),
        sa.Column("return_to", sa.String(500), nullable=False),
        sa.Column("remember_me", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("pending_subject", sa.String(255), nullable=True),
        sa.Column("pending_display_name", sa.String(80), nullable=True),
        sa.Column("registration_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_line_oauth_states_expires_at", "line_oauth_states", ["expires_at"])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM external_identities")).scalar_one():
        raise RuntimeError("已有LINE連結帳號，不可直接移除身分資料；請先確認帳號保留與登入方式")
    op.drop_table("line_oauth_states")
    op.drop_table("external_identities")
