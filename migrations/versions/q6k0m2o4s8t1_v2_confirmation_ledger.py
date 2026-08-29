"""Add neutral confirmation, effect, and resource ledgers for V2 plans."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q6k0m2o4s8t1"
down_revision: str | Sequence[str] | None = "p5j9l1n3q7r0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_confirmations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id", sa.Integer(), sa.ForeignKey("v2_plan_assignments.id"), nullable=False
        ),
        sa.Column("resource_key", sa.String(50), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("characters.id")),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.BigInteger()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("note", sa.Text()),
        sa.UniqueConstraint(
            "assignment_id", "resource_key", "action", name="uq_v2_confirmation_action"
        ),
    )
    op.create_table(
        "v2_effect_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "confirmation_id", sa.Integer(), sa.ForeignKey("v2_confirmations.id"), nullable=False
        ),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("slot_key", sa.String(30), nullable=False),
        sa.Column("resulting_category", sa.String(30), nullable=False),
        sa.Column("before_category", sa.String(30)),
        sa.Column("after_category", sa.String(30)),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "v2_resource_balances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("v2_plans.id"), nullable=False),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("resource_key", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("quantity >= 0", name="nonnegative_v2_resource_quantity"),
        sa.UniqueConstraint(
            "plan_id", "recipient_id", "resource_key", name="uq_v2_resource_balance"
        ),
    )


def downgrade() -> None:
    op.drop_table("v2_resource_balances")
    op.drop_table("v2_effect_ledger")
    op.drop_table("v2_confirmations")
