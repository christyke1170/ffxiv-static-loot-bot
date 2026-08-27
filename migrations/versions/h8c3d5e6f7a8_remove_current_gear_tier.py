"""Remove obsolete raid-tier ownership from current equipped gear."""

import sqlalchemy as sa
from alembic import op

revision = "h8c3d5e6f7a8"
down_revision = "g7b2c4d5e6f7"
branch_labels = None
depends_on = None

TABLE = "character_gear_slots"
COLUMN = "current_raid_tier_id"
FOREIGN_KEY = "fk_character_gear_slots_current_raid_tier_id_raid_tiers"


def upgrade() -> None:
    """Drop the obsolete column plus any named FK/indexes that reference it."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if COLUMN not in {column["name"] for column in inspector.get_columns(TABLE)}:
        return

    foreign_keys = [
        constraint
        for constraint in inspector.get_foreign_keys(TABLE)
        if COLUMN in constraint.get("constrained_columns", ()) and constraint.get("name")
    ]
    indexes = [
        index
        for index in inspector.get_indexes(TABLE)
        if COLUMN in index.get("column_names", ()) and index.get("name")
    ]
    with op.batch_alter_table(TABLE) as batch:
        for index in indexes:
            batch.drop_index(index["name"])
        for constraint in foreign_keys:
            batch.drop_constraint(constraint["name"], type_="foreignkey")
        batch.drop_column(COLUMN)


def downgrade() -> None:
    """Restore the former nullable relationship; discarded tier values stay null."""
    inspector = sa.inspect(op.get_bind())
    if COLUMN in {column["name"] for column in inspector.get_columns(TABLE)}:
        return
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column(COLUMN, sa.Integer(), nullable=True))
        batch.create_foreign_key(FOREIGN_KEY, "raid_tiers", [COLUMN], ["id"])
