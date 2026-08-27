"""reclear floor completions and append-only confirmation corrections"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c1f2d7a4e60"
down_revision: str | Sequence[str] | None = "f4d5b91a61c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reclear_floor_completions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reclear_week_id", sa.Integer(), nullable=False),
        sa.Column("reclear_group_id", sa.Integer(), nullable=False),
        sa.Column("raid_floor_id", sa.Integer(), nullable=False),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("actor_discord_user_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["reclear_week_id"], ["split_weeks.id"]),
        sa.ForeignKeyConstraint(["reclear_group_id"], ["split_groups.id"]),
        sa.ForeignKeyConstraint(["raid_floor_id"], ["raid_floors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reclear_week_id", "reclear_group_id", "raid_floor_id"),
    )
    with op.batch_alter_table("loot_confirmations") as batch:
        batch.add_column(sa.Column("supersedes_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_loot_confirmations_supersedes_id", "loot_confirmations", ["supersedes_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("loot_confirmations") as batch:
        batch.drop_constraint("fk_loot_confirmations_supersedes_id", type_="foreignkey")
        batch.drop_column("supersedes_id")
    op.drop_table("reclear_floor_completions")
