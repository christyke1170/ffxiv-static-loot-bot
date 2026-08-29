"""Store authoritative source snapshots for generated loot plans."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l2f6g8b0c3d4"
down_revision: str | Sequence[str] | None = "k1e5f7a9b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("loot_plans") as batch:
        batch.add_column(sa.Column("source_snapshot_version", sa.Integer()))
        batch.add_column(sa.Column("source_snapshot", sa.Text()))
        batch.add_column(sa.Column("source_state_hash", sa.String(length=64)))
        batch.create_index("ix_loot_plans_source_state_hash", ["source_state_hash"])


def downgrade() -> None:
    with op.batch_alter_table("loot_plans") as batch:
        batch.drop_index("ix_loot_plans_source_state_hash")
        batch.drop_column("source_state_hash")
        batch.drop_column("source_snapshot")
        batch.drop_column("source_snapshot_version")
