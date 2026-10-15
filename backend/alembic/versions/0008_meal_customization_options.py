"""Add meal customization groups and order option snapshots.

Revision ID: 0008_meal_customization_options
Revises: 0007_member_roster_claims
Create Date: 2026-08-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_meal_customization_options"
down_revision = "0007_member_roster_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meal_option_groups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("meal_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("min_selections", sa.Integer(), nullable=False),
        sa.Column("max_selections", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "min_selections >= 0",
            name=op.f("ck_meal_option_groups_min_selections_nonnegative"),
        ),
        sa.CheckConstraint(
            "max_selections >= 1",
            name=op.f("ck_meal_option_groups_max_selections_positive"),
        ),
        sa.CheckConstraint(
            "max_selections >= min_selections",
            name=op.f("ck_meal_option_groups_selection_range_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["meal_id"],
            ["meals.id"],
            name=op.f("fk_meal_option_groups_meal_id_meals"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meal_option_groups")),
        sa.UniqueConstraint(
            "meal_id",
            "name",
            name=op.f("uq_meal_option_groups_meal_id"),
        ),
    )
    op.create_index(
        op.f("ix_meal_option_groups_meal_id"),
        "meal_option_groups",
        ["meal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_meal_option_groups_is_active"),
        "meal_option_groups",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "meal_options",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("price_delta", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "price_delta >= 0",
            name=op.f("ck_meal_options_price_delta_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["meal_option_groups.id"],
            name=op.f("fk_meal_options_group_id_meal_option_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meal_options")),
        sa.UniqueConstraint(
            "group_id",
            "name",
            name=op.f("uq_meal_options_group_id"),
        ),
    )
    op.create_index(
        op.f("ix_meal_options_group_id"),
        "meal_options",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_meal_options_is_active"),
        "meal_options",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "order_item_options",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_item_id", sa.String(length=36), nullable=False),
        sa.Column("source_meal_option_id", sa.String(length=36), nullable=True),
        sa.Column("group_name", sa.String(length=120), nullable=False),
        sa.Column("option_name", sa.String(length=120), nullable=False),
        sa.Column("price_delta", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "price_delta >= 0",
            name=op.f("ck_order_item_options_price_delta_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name=op.f("fk_order_item_options_order_item_id_order_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_meal_option_id"],
            ["meal_options.id"],
            name=op.f(
                "fk_order_item_options_source_meal_option_id_meal_options"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_item_options")),
    )
    op.create_index(
        op.f("ix_order_item_options_order_item_id"),
        "order_item_options",
        ["order_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_order_item_options_source_meal_option_id"),
        "order_item_options",
        ["source_meal_option_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_order_item_options_source_meal_option_id"),
        table_name="order_item_options",
    )
    op.drop_index(
        op.f("ix_order_item_options_order_item_id"),
        table_name="order_item_options",
    )
    op.drop_table("order_item_options")
    op.drop_index(
        op.f("ix_meal_options_is_active"),
        table_name="meal_options",
    )
    op.drop_index(
        op.f("ix_meal_options_group_id"),
        table_name="meal_options",
    )
    op.drop_table("meal_options")
    op.drop_index(
        op.f("ix_meal_option_groups_is_active"),
        table_name="meal_option_groups",
    )
    op.drop_index(
        op.f("ix_meal_option_groups_meal_id"),
        table_name="meal_option_groups",
    )
    op.drop_table("meal_option_groups")
