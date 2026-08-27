"""Track the raid tier of current gear for typed gear-board statuses."""

import sqlalchemy as sa
from alembic import op

revision = "g7b2c4d5e6f7"
down_revision = "a91c6e4d2b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE gearclassification ADD VALUE IF NOT EXISTS 'EX_WEAPON'")
    with op.batch_alter_table("character_gear_slots") as batch:
        batch.add_column(sa.Column("current_raid_tier_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_character_gear_slots_current_raid_tier_id_raid_tiers",
            "raid_tiers",
            ["current_raid_tier_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("character_gear_slots") as batch:
        batch.drop_constraint(
            "fk_character_gear_slots_current_raid_tier_id_raid_tiers", type_="foreignkey"
        )
        batch.drop_column("current_raid_tier_id")
