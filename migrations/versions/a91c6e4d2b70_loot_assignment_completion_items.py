"""Normalize bundled loot-assignment completion items.

Revision ID: a91c6e4d2b70
Revises: e8a6f30b2c14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a91c6e4d2b70"
down_revision: str | Sequence[str] | None = "e8a6f30b2c14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loot_assignment_completion_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("loot_assignment_id", sa.Integer(), nullable=False),
        sa.Column("bis_set_item_id", sa.Integer(), nullable=False),
        sa.Column("intended_final_item_id", sa.Integer(), nullable=False),
        sa.Column("previous_gear_item_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["bis_set_item_id"], ["bis_set_items.id"]),
        sa.ForeignKeyConstraint(["intended_final_item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["loot_assignment_id"], ["loot_assignments.id"]),
        sa.ForeignKeyConstraint(["previous_gear_item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "loot_assignment_id",
            "bis_set_item_id",
            name="uq_loot_assignment_completion_item",
        ),
    )
    # Preserve the canonical completion target for every existing assignment.
    op.execute(
        sa.text(
            """
            INSERT INTO loot_assignment_completion_items
                (loot_assignment_id, bis_set_item_id, intended_final_item_id)
            SELECT id, intended_bis_set_item_id, intended_final_item_id
            FROM loot_assignments
            WHERE intended_bis_set_item_id IS NOT NULL
              AND intended_final_item_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_table("loot_assignment_completion_items")
