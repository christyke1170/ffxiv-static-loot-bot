"""Store current gear classification independently from exact items."""

import sqlalchemy as sa
from alembic import op

revision = "e8a6f30b2c14"
down_revision = "c2f4d8e1a901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("character_gear_slots") as batch:
        batch.add_column(
            sa.Column(
                "current_classification",
                sa.Enum(
                    "SAVAGE",
                    "AUGMENTED_TOME",
                    "TOME",
                    "CRAFTED",
                    "CATCHUP",
                    "RELIC",
                    "NORMAL_RAID",
                    "EITHER",
                    "OTHER",
                    "NOT_APPLICABLE",
                    name="gearclassification",
                ),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("character_gear_slots") as batch:
        batch.drop_column("current_classification")
