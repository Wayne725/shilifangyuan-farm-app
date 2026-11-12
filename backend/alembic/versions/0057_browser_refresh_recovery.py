"""Track browser refresh generation and a short-lived recovery proof."""
from alembic import op
import sqlalchemy as sa

revision = "0057_browser_refresh_recovery"
down_revision = "0056_payment_return_storefront"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    op.create_table(
        "browser_refresh_selections",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column(
            "current_session_id",
            sa.String(36),
            sa.ForeignKey("refresh_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("predecessor_id", sa.String(36)),
        sa.Column("recovery_proof_hash", sa.String(64)),
        sa.Column("recovery_until", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_browser_refresh_selections_current_session_id",
        "browser_refresh_selections",
        ["current_session_id"],
    )


def downgrade():
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE browser_refresh_selections IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        # Start the writer transaction before checking; SQLite ignores row locks.
        op.execute("UPDATE browser_refresh_selections SET id = id WHERE 0")
    has_active_selection = op.get_bind().scalar(sa.text(
        "SELECT EXISTS ("
        "SELECT 1 FROM browser_refresh_selections AS selection "
        "JOIN refresh_sessions AS session ON session.id = selection.current_session_id "
        "WHERE session.revoked_at IS NULL AND session.expires_at > CURRENT_TIMESTAMP)"
    ))
    if has_active_selection:
        raise RuntimeError("仍有有效的新版瀏覽器登入，請保留工作階段並向前修復")
    op.drop_table("browser_refresh_selections")
