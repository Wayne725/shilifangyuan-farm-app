"""Preserve both remediation and commerce histories without rewriting either branch."""
revision = "0049_merge_commerce_remediation"
down_revision = ("0048_merge_remediation_print", "0045_walk_in_sales")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
