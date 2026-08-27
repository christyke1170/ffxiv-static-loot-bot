"""Make current equipped gear classification-only."""

import sqlalchemy as sa
from alembic import op

revision = "j9d4e6f7a8b9"
down_revision = "h8c3d5e6f7a8"
branch_labels = None
depends_on = None

CURRENT = ("CRAFTED", "EX_WEAPON", "SAVAGE", "TOME", "AUGMENTED_TOME", "GARBAGE")
ALL = (
    "SAVAGE",
    "AUGMENTED_TOME",
    "TOME",
    "CRAFTED",
    "EX_WEAPON",
    "GARBAGE",
    "CATCHUP",
    "RELIC",
    "NORMAL_RAID",
    "EITHER",
    "OTHER",
    "NOT_APPLICABLE",
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE character_gear_slots SET current_classification = 'GARBAGE' "
            "WHERE current_classification IS NULL OR current_classification NOT IN "
            "('CRAFTED','EX_WEAPON','SAVAGE','TOME','AUGMENTED_TOME')"
        )
    )
    enum = sa.Enum(*ALL, name="gearclassification")
    with op.batch_alter_table("character_gear_slots") as batch:
        batch.alter_column("current_classification", existing_type=enum, nullable=False)
        batch.drop_column("item_id")
        batch.drop_column("note")
    with op.batch_alter_table("loot_confirmations") as batch:
        batch.add_column(sa.Column("previous_gear_classification", enum, nullable=True))
    with op.batch_alter_table("loot_assignment_completion_items") as batch:
        batch.add_column(sa.Column("previous_gear_classification", enum, nullable=True))


def downgrade() -> None:
    enum = sa.Enum(*ALL, name="gearclassification")
    with op.batch_alter_table("loot_assignment_completion_items") as batch:
        batch.drop_column("previous_gear_classification")
    with op.batch_alter_table("loot_confirmations") as batch:
        batch.drop_column("previous_gear_classification")
    with op.batch_alter_table("character_gear_slots") as batch:
        batch.add_column(sa.Column("note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("item_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_character_gear_slots_item_id_items", "items", ["item_id"], ["id"]
        )
        batch.alter_column("current_classification", existing_type=enum, nullable=True)
