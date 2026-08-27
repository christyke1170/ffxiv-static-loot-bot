"""Persist Discord users' selected static."""

import sqlalchemy as sa
from alembic import op

revision = "c2f4d8e1a901"
down_revision = "b7e4c1d902af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_static_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.Integer(), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("static_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["discord_guilds.id"]),
        sa.ForeignKeyConstraint(["static_id"], ["statics.id"]),
        sa.UniqueConstraint("guild_id", "discord_user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_static_preferences")
