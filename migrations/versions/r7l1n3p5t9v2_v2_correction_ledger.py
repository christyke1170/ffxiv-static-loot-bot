"""Add append-only administrator correction ledger for V2 actions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r7l1n3p5t9v2"
down_revision: str | Sequence[str] | None = "q6k0m2o4s8t1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "confirmation_id", sa.Integer(), sa.ForeignKey("v2_confirmations.id"), nullable=False
        ),
        sa.Column("correction_type", sa.String(30), nullable=False),
        sa.Column("corrected_success", sa.Boolean()),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("v2_corrections")
