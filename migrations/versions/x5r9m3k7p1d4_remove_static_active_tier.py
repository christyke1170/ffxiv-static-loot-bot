"""Remove the obsolete Static active-tier pointer.

Historical tier-owned records retain their own tier foreign keys.  Downgrade
restores the nullable compatibility column, but intentionally does not
reconstruct obsolete active selections.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x5r9m3k7p1d4"
down_revision: str | Sequence[str] | None = "w6r0t4y8u2i5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("statics") as batch:
        batch.drop_column("active_raid_tier_id")


def downgrade() -> None:
    with op.batch_alter_table("statics") as batch:
        batch.add_column(sa.Column("active_raid_tier_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_statics_active_raid_tier_id_raid_tiers",
            "raid_tiers",
            ["active_raid_tier_id"],
            ["id"],
        )
