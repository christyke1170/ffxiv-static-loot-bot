"""Add the V2 state fingerprint lookup index."""

from collections.abc import Sequence

from alembic import op

revision: str = "t9n3p7r1v5x8"
down_revision: str | Sequence[str] | None = "s8m2o4q6u0w3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_v2_plans_state_fingerprint",
        "v2_plans",
        ["state_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_v2_plans_state_fingerprint", table_name="v2_plans")
