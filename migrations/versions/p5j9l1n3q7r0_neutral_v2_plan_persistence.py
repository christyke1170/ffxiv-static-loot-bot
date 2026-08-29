"""Add lossless neutral persistence for Regular and Split V2 proposals."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p5j9l1n3q7r0"
down_revision: str | Sequence[str] | None = "n4h8j0k2l6m9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("static_id", sa.Integer(), sa.ForeignKey("statics.id"), nullable=False),
        sa.Column("reclear_week_id", sa.Integer(), sa.ForeignKey("split_weeks.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("week_number", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("state_fingerprint", sa.String(64), nullable=False),
        sa.Column("warnings_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("score_json", sa.Text()),
        sa.Column("partitions_evaluated", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("reclear_week_id", name="uq_v2_plans_week"),
    )
    op.create_table(
        "v2_plan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("v2_plans.id"), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("source_group_id", sa.Integer()),
        sa.UniqueConstraint("plan_id", "run_number"),
    )
    op.create_table(
        "v2_plan_participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("v2_plan_runs.id"), nullable=False),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("designation", sa.String(10), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "character_id"),
    )
    op.create_table(
        "v2_plan_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("v2_plans.id"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("v2_plan_runs.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("floor_number", sa.Integer(), nullable=False),
        sa.Column("loot_key", sa.String(100), nullable=False),
        sa.Column("primary_slot", sa.String(30)),
        sa.Column("material_key", sa.String(100)),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("characters.id")),
        sa.Column("recipient_job", sa.String(10)),
        sa.Column("recipient_kind", sa.String(10)),
        sa.Column("owned_alt_id", sa.Integer(), sa.ForeignKey("characters.id")),
        sa.Column("hierarchy_position", sa.Integer()),
        sa.Column("disposition", sa.String(20), nullable=False),
        sa.Column("resource_quantity", sa.Integer(), nullable=False),
        sa.Column("fairness_count", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("score_json", sa.Text()),
    )
    op.create_table(
        "v2_plan_effects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id", sa.Integer(), sa.ForeignKey("v2_plan_assignments.id"), nullable=False
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("slot_key", sa.String(30), nullable=False),
        sa.Column("resulting_category", sa.String(30), nullable=False),
    )
    op.create_table(
        "v2_plan_unassigned",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("v2_plans.id"), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("floor_number", sa.Integer(), nullable=False),
        sa.Column("loot_key", sa.String(100), nullable=False),
        sa.Column("primary_slot", sa.String(30)),
        sa.Column("material_key", sa.String(100)),
        sa.Column("reason", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("v2_plan_unassigned")
    op.drop_table("v2_plan_effects")
    op.drop_table("v2_plan_assignments")
    op.drop_table("v2_plan_participants")
    op.drop_table("v2_plan_runs")
    op.drop_table("v2_plans")
