"""weekly loot planning

Revision ID: f4d5b91a61c2
Revises: da68811c808c
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4d5b91a61c2"
down_revision: str | Sequence[str] | None = "da68811c808c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("loot_assignments") as batch:
        batch.add_column(sa.Column("reclear_group_id", sa.Integer()))
        batch.add_column(sa.Column("recipient_owns_base_tome_item", sa.Boolean()))
        batch.add_column(sa.Column("hierarchy_position", sa.Integer()))
        batch.create_foreign_key(
            "fk_loot_assignments_reclear_group_id_split_groups",
            "split_groups",
            ["reclear_group_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_loot_assignment_expected_drop",
            [
                "loot_plan_id",
                "reclear_group_id",
                "raid_floor_id",
                "loot_type_id",
                "expected_drop_instance",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("loot_assignments") as batch:
        batch.drop_constraint("uq_loot_assignment_expected_drop", type_="unique")
        batch.drop_constraint(
            "fk_loot_assignments_reclear_group_id_split_groups", type_="foreignkey"
        )
        batch.drop_column("hierarchy_position")
        batch.drop_column("recipient_owns_base_tome_item")
        batch.drop_column("reclear_group_id")
