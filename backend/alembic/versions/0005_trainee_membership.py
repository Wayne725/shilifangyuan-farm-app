"""Add customer and trainee identities.

Revision ID: 0005_trainee_membership
Revises: 0004_cooperative_core
Create Date: 2026-08-10
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0005_trainee_membership"
down_revision = "0004_cooperative_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("customer_number", sa.String(length=32), nullable=True),
    )
    op.create_index(
        op.f("ix_users_customer_number"),
        "users",
        ["customer_number"],
        unique=True,
    )
    op.add_column(
        "memberships",
        sa.Column("trainee_number", sa.String(length=32), nullable=True),
    )
    op.create_index(
        op.f("ix_memberships_trainee_number"),
        "memberships",
        ["trainee_number"],
        unique=True,
    )

    users = sa.table(
        "users",
        sa.column("id", sa.String(length=36)),
        sa.column("customer_number", sa.String(length=32)),
        sa.column("user_role", sa.String(length=20)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    counters: dict[int, int] = defaultdict(int)
    rows = bind.execute(
        sa.select(users.c.id, users.c.created_at)
        .where(users.c.user_role == "customer")
        .order_by(
            users.c.created_at,
            users.c.id,
        )
    ).all()
    fallback_year = datetime.now(timezone.utc).year
    for user_id, created_at in rows:
        year = created_at.year if created_at is not None else fallback_year
        counters[year] += 1
        bind.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(customer_number=f"SLF-C-{year}-{counters[year]:04d}")
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE orders SET membership_type_snapshot = 'nonmember' "
            "WHERE membership_type_snapshot = 'trainee'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE users SET membership_type = 'nonmember' "
            "WHERE membership_type = 'trainee'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE memberships SET status = 'pending_payment' "
            "WHERE status = 'trainee'"
        )
    )
    op.drop_index(
        op.f("ix_memberships_trainee_number"),
        table_name="memberships",
    )
    op.drop_column("memberships", "trainee_number")
    op.drop_index(
        op.f("ix_users_customer_number"),
        table_name="users",
    )
    op.drop_column("users", "customer_number")
