"""Preserve meeting history while allowing managed meeting categories."""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0036_meeting_categories"
down_revision = "0035_order_amendments"
branch_labels = None
depends_on = None

CATEGORIES = (
    ("00000000-0000-4000-8000-000000000001", "社員大會", "general_assembly", 0),
    ("00000000-0000-4000-8000-000000000002", "社務會議", "affairs", 1),
)


def upgrade():
    categories = op.create_table(
        "meeting_categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("legacy_type", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_meeting_categories_name"),
        sa.UniqueConstraint("legacy_type", name="uq_meeting_categories_legacy_type"),
        sa.CheckConstraint("sort_order >= 0", name="sort_order_nonnegative"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 80 AND name = trim(name)", name="name_valid"),
        sa.CheckConstraint("legacy_type IS NULL OR legacy_type IN ('general_assembly', 'affairs')", name="legacy_type_valid"),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(categories, [dict(id=key, name=name, sort_order=sort_order, is_active=True,
        legacy_type=legacy_type, created_at=now, updated_at=now)
        for key, name, legacy_type, sort_order in CATEGORIES])
    op.add_column("meetings", sa.Column("category_id", sa.String(36), nullable=True))
    connection = op.get_bind()
    for category_id, _, legacy_type, _ in CATEGORIES:
        connection.execute(sa.text("UPDATE meetings SET category_id=:category_id WHERE meeting_type=:legacy_type"),
                           {"category_id": category_id, "legacy_type": legacy_type})
    with op.batch_alter_table("meetings") as batch:
        batch.alter_column("category_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key(op.f("fk_meetings_category_id_meeting_categories"), "meeting_categories", ["category_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_meetings_category_id", "meetings", ["category_id"])


def downgrade():
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, name, legacy_type, sort_order, is_active FROM meeting_categories")).all()
    original = {(key, name, legacy_type, sort_order, True) for key, name, legacy_type, sort_order in CATEGORIES}
    incompatible_meetings = connection.scalar(sa.text(
        "SELECT COUNT(*) FROM meetings m JOIN meeting_categories c ON c.id=m.category_id "
        "WHERE c.legacy_type IS NULL OR m.meeting_type <> c.legacy_type"))
    if {tuple(row) for row in rows} != original or incompatible_meetings:
        raise RuntimeError("會議類別已有自訂設定或歷史紀錄，無法安全降版")
    with op.batch_alter_table("meetings") as batch:
        batch.drop_index("ix_meetings_category_id")
        batch.drop_constraint(op.f("fk_meetings_category_id_meeting_categories"), type_="foreignkey")
        batch.drop_column("category_id")
    op.drop_table("meeting_categories")
