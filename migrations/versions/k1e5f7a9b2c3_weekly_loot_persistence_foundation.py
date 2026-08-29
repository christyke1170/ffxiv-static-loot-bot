"""Add the weekly loot planning persistence foundation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k1e5f7a9b2c3"
down_revision: str | Sequence[str] | None = "j9d4e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLAN_STATUS = sa.Enum("DRAFT", "READY", "APPLIED", "CANCELLED", name="weeklylootplanstatus")
DISPOSITION = sa.Enum("ASSIGNED", "FREE_ROLL", "UNASSIGNED", name="plannedlootdisposition")
CHARACTER_KIND = sa.Enum("MAIN", "ALT", name="characterkind")
CLEAR_MODE = sa.Enum("REGULAR", "SPLIT", name="clearmode")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        PLAN_STATUS.create(bind, checkfirst=True)
        DISPOSITION.create(bind, checkfirst=True)
    with op.batch_alter_table("loot_plans") as batch:
        batch.create_unique_constraint("uq_loot_plans_id_reclear_week", ["id", "split_week_id"])
        batch.add_column(sa.Column("mode", CLEAR_MODE, server_default="REGULAR", nullable=False))
        batch.add_column(sa.Column("status", PLAN_STATUS, server_default="DRAFT", nullable=False))
        batch.add_column(sa.Column("created_by_discord_user_id", sa.BigInteger()))
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch.add_column(sa.Column("applied_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_loot_plans_mode", ["mode"])
        batch.create_index("ix_loot_plans_status", ["status"])

    op.execute(
        "UPDATE loot_plans SET mode = ("
        "SELECT clear_mode FROM split_weeks WHERE split_weeks.id = loot_plans.split_week_id"
        ")"
    )

    op.create_table(
        "loot_plan_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("loot_plan_id", sa.Integer(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.CheckConstraint("run_number > 0", name="positive_run_number"),
        sa.ForeignKeyConstraint(["loot_plan_id"], ["loot_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "loot_plan_id", name="uq_loot_plan_runs_id_plan"),
        sa.UniqueConstraint("loot_plan_id", "name", name="uq_loot_plan_runs_plan_name_initial"),
        sa.UniqueConstraint(
            "loot_plan_id", "run_number", name="uq_loot_plan_runs_plan_run_number_initial"
        ),
    )
    op.create_table(
        "loot_plan_participants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("designation", CHARACTER_KIND, nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"]),
        sa.ForeignKeyConstraint(["plan_run_id"], ["loot_plan_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_run_id", "character_id"),
    )

    with op.batch_alter_table("loot_assignments") as batch:
        batch.add_column(sa.Column("plan_run_id", sa.Integer()))
        batch.add_column(sa.Column("recipient_designation", CHARACTER_KIND))
        batch.add_column(
            sa.Column("disposition", DISPOSITION, server_default="UNASSIGNED", nullable=False)
        )
        batch.add_column(sa.Column("paired_assignment_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_loot_assignments_plan_run_plan",
            "loot_plan_runs",
            ["plan_run_id", "loot_plan_id"],
            ["id", "loot_plan_id"],
        )
        batch.create_foreign_key(
            "fk_loot_assignments_paired_assignment_id_loot_assignments",
            "loot_assignments",
            ["paired_assignment_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_loot_assignment_run_expected_drop",
            ["plan_run_id", "raid_floor_id", "loot_type_id", "expected_drop_instance"],
        )
        batch.create_unique_constraint(
            "uq_loot_assignments_paired_assignment_id", ["paired_assignment_id"]
        )
        batch.create_check_constraint(
            "paired_assignment_not_self",
            "paired_assignment_id IS NULL OR paired_assignment_id != id",
        )
        batch.create_check_constraint(
            "assigned_has_recipient",
            "disposition != 'ASSIGNED' OR intended_character_id IS NOT NULL",
        )
        batch.create_check_constraint(
            "designation_requires_recipient",
            "intended_character_id IS NOT NULL OR recipient_designation IS NULL",
        )
        batch.create_index("ix_loot_assignments_plan_run_id", ["plan_run_id"])

    op.create_table(
        "confirmed_reclear_material_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("loot_assignment_id", sa.Integer(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("augmentation_material_type_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("confirmed_by_discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name="positive_quantity"),
        sa.ForeignKeyConstraint(
            ["augmentation_material_type_id"], ["augmentation_material_types.id"]
        ),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"]),
        sa.ForeignKeyConstraint(["loot_assignment_id"], ["loot_assignments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("loot_assignment_id"),
    )
    op.create_index(
        "ix_confirmed_reclear_material_grants_character_id",
        "confirmed_reclear_material_grants",
        ["character_id"],
    )
    op.create_index(
        "ix_confirmed_grants_material_type_id",
        "confirmed_reclear_material_grants",
        ["augmentation_material_type_id"],
    )
    op.create_index(
        "ix_confirmed_reclear_material_grants_confirmed_at",
        "confirmed_reclear_material_grants",
        ["confirmed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_confirmed_reclear_material_grants_confirmed_at",
        table_name="confirmed_reclear_material_grants",
    )
    op.drop_index(
        "ix_confirmed_grants_material_type_id",
        table_name="confirmed_reclear_material_grants",
    )
    op.drop_index(
        "ix_confirmed_reclear_material_grants_character_id",
        table_name="confirmed_reclear_material_grants",
    )
    op.drop_table("confirmed_reclear_material_grants")

    with op.batch_alter_table("loot_assignments") as batch:
        batch.drop_index("ix_loot_assignments_plan_run_id")
        batch.drop_constraint("designation_requires_recipient", type_="check")
        batch.drop_constraint("assigned_has_recipient", type_="check")
        batch.drop_constraint("paired_assignment_not_self", type_="check")
        batch.drop_constraint("uq_loot_assignments_paired_assignment_id", type_="unique")
        batch.drop_constraint("uq_loot_assignment_run_expected_drop", type_="unique")
        batch.drop_constraint(
            "fk_loot_assignments_paired_assignment_id_loot_assignments",
            type_="foreignkey",
        )
        batch.drop_constraint("fk_loot_assignments_plan_run_plan", type_="foreignkey")
        batch.drop_column("paired_assignment_id")
        batch.drop_column("disposition")
        batch.drop_column("recipient_designation")
        batch.drop_column("plan_run_id")

    op.drop_table("loot_plan_participants")
    op.drop_table("loot_plan_runs")

    with op.batch_alter_table("loot_plans") as batch:
        batch.drop_index("ix_loot_plans_status")
        batch.drop_index("ix_loot_plans_mode")
        batch.drop_column("cancelled_at")
        batch.drop_column("applied_at")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
        batch.drop_column("created_by_discord_user_id")
        batch.drop_column("status")
        batch.drop_column("mode")
        batch.drop_constraint("uq_loot_plans_id_reclear_week", type_="unique")
