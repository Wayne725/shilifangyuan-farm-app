"""Record consent for LINE profile data use."""

from alembic import op
import sqlalchemy as sa

revision = "0024_line_data_consent"
down_revision = "0023_meal_option_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("external_identities") as batch:
        batch.add_column(sa.Column("data_consent_version", sa.String(40), nullable=True))
        batch.add_column(sa.Column("data_consented_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("line_oauth_states") as batch:
        batch.add_column(sa.Column("data_consent_version", sa.String(40), nullable=True))
        batch.add_column(sa.Column("data_consented_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("line_oauth_states") as batch:
        batch.drop_column("data_consented_at")
        batch.drop_column("data_consent_version")
    with op.batch_alter_table("external_identities") as batch:
        batch.drop_column("data_consented_at")
        batch.drop_column("data_consent_version")
