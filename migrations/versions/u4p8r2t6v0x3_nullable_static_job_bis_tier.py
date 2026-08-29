"""Allow canonical Static + Job BiS rows to be tier-neutral.

Historical tier-owned BiS rows retain their raid tier.  New canonical rows
may leave the legacy tier foreign key null until the legacy BiS storage is
retired.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u4p8r2t6v0x3"
down_revision: str | Sequence[str] | None = "t9n3p7r1v5x8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table("bis_sets") as batch:
            batch.alter_column(
                "raid_tier_id",
                existing_type=sa.Integer(),
                existing_nullable=False,
                nullable=True,
            )
    else:
        op.alter_column(
            "bis_sets",
            "raid_tier_id",
            existing_type=sa.Integer(),
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    null_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM bis_sets WHERE raid_tier_id IS NULL")
    ).scalar_one()
    if null_count:
        raise RuntimeError(
            "Cannot restore bis_sets.raid_tier_id NOT NULL while "
            f"{null_count} canonical Static + Job BiS row(s) have no historical tier."
        )
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table("bis_sets") as batch:
            batch.alter_column(
                "raid_tier_id",
                existing_type=sa.Integer(),
                existing_nullable=True,
                nullable=False,
            )
    else:
        op.alter_column(
            "bis_sets",
            "raid_tier_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )
