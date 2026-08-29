"""Store the actor that created an orchestration-generated V2 plan."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s8m2o4q6u0w3"
down_revision: str | Sequence[str] | None = "r7l1n3p5t9v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("v2_plans", sa.Column("actor_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("v2_plans", "actor_id")
