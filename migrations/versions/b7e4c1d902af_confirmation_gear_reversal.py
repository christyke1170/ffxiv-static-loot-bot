"""store prior gear for safe confirmation reversal"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e4c1d902af"
down_revision: str | Sequence[str] | None = "9c1f2d7a4e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("loot_confirmations") as batch:
        batch.add_column(sa.Column("previous_gear_item_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_loot_confirmations_previous_gear_item_id_items",
            "items",
            ["previous_gear_item_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("loot_confirmations") as batch:
        batch.drop_constraint(
            "fk_loot_confirmations_previous_gear_item_id_items", type_="foreignkey"
        )
        batch.drop_column("previous_gear_item_id")
