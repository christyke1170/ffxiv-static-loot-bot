"""Ensure neutral floor uniqueness indexes are represented in migration state.

The indexes are conditional because historical rows use ``raid_floor_id``
while neutral rows use logical ``floor_number``.  Existing databases may
already have them from the neutral-week migration, so creation is idempotent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "y6s0n4l8q2e5"
down_revision: str | Sequence[str] | None = "x5r9m3k7p1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        index["name"]
        for table in ("weekly_lockouts", "reclear_floor_completions")
        for index in inspector.get_indexes(table)
    }


def upgrade() -> None:
    existing = _index_names()
    if "uq_weekly_lockout_neutral_floor" not in existing:
        op.create_index(
            "uq_weekly_lockout_neutral_floor",
            "weekly_lockouts",
            ["character_id", "floor_number", "week_start"],
            unique=True,
            sqlite_where=sa.text("floor_number IS NOT NULL"),
            postgresql_where=sa.text("floor_number IS NOT NULL"),
        )
    if "uq_reclear_completion_neutral_floor" not in existing:
        op.create_index(
            "uq_reclear_completion_neutral_floor",
            "reclear_floor_completions",
            ["reclear_week_id", "reclear_group_id", "floor_number"],
            unique=True,
            sqlite_where=sa.text("floor_number IS NOT NULL"),
            postgresql_where=sa.text("floor_number IS NOT NULL"),
        )


def downgrade() -> None:
    existing = _index_names()
    if "uq_reclear_completion_neutral_floor" in existing:
        op.drop_index("uq_reclear_completion_neutral_floor", table_name="reclear_floor_completions")
    if "uq_weekly_lockout_neutral_floor" in existing:
        op.drop_index("uq_weekly_lockout_neutral_floor", table_name="weekly_lockouts")
