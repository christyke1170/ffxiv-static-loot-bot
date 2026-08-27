"""weekly reclear and bis imports

Revision ID: da68811c808c
Revises: 416497b5ec9c
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "da68811c808c"
down_revision: str | Sequence[str] | None = "416497b5ec9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLEAR_MODE = sa.Enum("REGULAR", "SPLIT", name="clearmode")
WORKFLOW_STATE = sa.Enum(
    "DRAFT",
    "PLANNED",
    "IN_PROGRESS",
    "AWAITING_CONFIRMATION",
    "CONFIRMED",
    "CLOSED",
    "CANCELLED",
    name="reclearworkflowstate",
)
ASSIGNMENT_STATE = sa.Enum(
    "PROPOSED",
    "CONFIRMED",
    "RECEIVED",
    "REDEEMED_CORRECTLY",
    "RECEIPT_FAILED",
    "REDEMPTION_ERROR",
    "LEFTOVER",
    "FREE_ROLL",
    "CANCELLED",
    name="lootassignmentstate",
)
CONFIRMATION_TYPE = sa.Enum(
    "RECEIVED", "REDEEMED_CORRECTLY", "AUGMENT_APPLIED", name="lootconfirmationtype"
)
ERROR_TYPE = sa.Enum(
    "INTENDED_RECIPIENT_DID_NOT_RECEIVE",
    "WRONG_RECIPIENT",
    "WRONG_COFFER_REDEMPTION",
    "AUGMENT_NOT_APPLIED",
    "USER_ENTRY_ERROR",
    "OTHER",
    name="distributionerrortype",
)


def upgrade() -> None:
    op.add_column("statics", sa.Column("active_raid_tier_id", sa.Integer()))
    with op.batch_alter_table("statics") as batch:
        batch.create_foreign_key(
            "fk_statics_active_raid_tier_id_raid_tiers",
            "raid_tiers",
            ["active_raid_tier_id"],
            ["id"],
        )
    op.add_column("jobs", sa.Column("role", sa.String(30), server_default="Unknown"))
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("role", nullable=False, server_default=None)

    op.add_column("bis_sets", sa.Column("gear_set_url", sa.String(500)))
    op.add_column("bis_sets", sa.Column("description", sa.Text()))
    op.add_column(
        "bis_sets", sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False)
    )
    with op.batch_alter_table("bis_set_items") as batch:
        batch.add_column(sa.Column("raid_floor_id", sa.Integer()))
        batch.add_column(sa.Column("loot_type_id", sa.Integer()))
        batch.add_column(sa.Column("base_tome_item_id", sa.Integer()))
        batch.add_column(sa.Column("tome_cost", sa.Integer()))
        batch.add_column(sa.Column("augmentation_material_type_id", sa.Integer()))
        batch.add_column(sa.Column("book_cost", sa.Integer()))
        batch.add_column(sa.Column("notes", sa.Text()))
        batch.create_foreign_key(
            "fk_bis_set_items_raid_floor_id_raid_floors", "raid_floors", ["raid_floor_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_bis_set_items_loot_type_id_loot_types", "loot_types", ["loot_type_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_bis_set_items_base_tome_item_id_items", "items", ["base_tome_item_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_bis_set_items_augmentation_material_type_id_augmentation_material_types",
            "augmentation_material_types",
            ["augmentation_material_type_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "nonnegative_tome_cost", "tome_cost IS NULL OR tome_cost >= 0"
        )
        batch.create_check_constraint(
            "nonnegative_book_cost", "book_cost IS NULL OR book_cost >= 0"
        )

    with op.batch_alter_table("character_gear_slots") as batch:
        batch.add_column(
            sa.Column("manually_complete", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch.add_column(sa.Column("note", sa.Text()))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
    with op.batch_alter_table("character_floor_book_balances") as batch:
        batch.add_column(sa.Column("earned", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("spent", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(
            sa.Column("manual_adjustment", sa.Integer(), server_default="0", nullable=False)
        )
    op.execute("UPDATE character_floor_book_balances SET earned = quantity")
    with op.batch_alter_table("character_floor_book_balances") as batch:
        batch.drop_constraint("nonnegative_quantity", type_="check")
        batch.drop_column("quantity")
        batch.create_check_constraint("nonnegative_earned", "earned >= 0")
        batch.create_check_constraint("nonnegative_spent", "spent >= 0")

    op.create_table(
        "job_hierarchies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("static_id", sa.Integer(), sa.ForeignKey("statics.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100)),
        sa.Column("active_marker", sa.Boolean()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.CheckConstraint(
            "active_marker IS NULL OR active_marker = 1",
            name="valid_active_marker",
        ),
        sa.UniqueConstraint("static_id", "version", name="uq_job_hierarchies_static_version"),
        sa.UniqueConstraint("static_id", "active_marker", name="uq_job_hierarchies_static_active"),
    )
    op.create_table(
        "job_hierarchy_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "hierarchy_id", sa.Integer(), sa.ForeignKey("job_hierarchies.id"), nullable=False
        ),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position > 0", name="positive_position"),
        sa.UniqueConstraint(
            "hierarchy_id", "job_id", name="uq_job_hierarchy_entries_hierarchy_job"
        ),
        sa.UniqueConstraint(
            "hierarchy_id", "position", name="uq_job_hierarchy_entries_hierarchy_position"
        ),
    )

    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM split_weeks")):
        connection.execute(
            sa.text(
                "INSERT INTO raid_tiers (code, name, active) VALUES "
                "('LEGACY_MIGRATED', 'Legacy migrated weekly records', 0)"
            )
        )
    legacy_tier = "(SELECT id FROM raid_tiers WHERE code = 'LEGACY_MIGRATED')"
    with op.batch_alter_table("split_weeks") as batch:
        batch.add_column(sa.Column("raid_tier_id", sa.Integer()))
        batch.add_column(sa.Column("hierarchy_id", sa.Integer()))
        batch.add_column(
            sa.Column("clear_mode", CLEAR_MODE, server_default="SPLIT", nullable=False)
        )
        batch.add_column(
            sa.Column("workflow_state", WORKFLOW_STATE, server_default="DRAFT", nullable=False)
        )
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch.add_column(sa.Column("finalized_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("notes", sa.Text()))
    op.execute(f"UPDATE split_weeks SET raid_tier_id = {legacy_tier} WHERE raid_tier_id IS NULL")
    with op.batch_alter_table("split_weeks") as batch:
        batch.alter_column("raid_tier_id", nullable=False)
        batch.create_foreign_key(
            "fk_split_weeks_raid_tier_id_raid_tiers", "raid_tiers", ["raid_tier_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_split_weeks_hierarchy_id_job_hierarchies",
            "job_hierarchies",
            ["hierarchy_id"],
            ["id"],
        )

    op.create_table(
        "character_bis_selections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("raid_tier_id", sa.Integer(), sa.ForeignKey("raid_tiers.id"), nullable=False),
        sa.Column("bis_set_id", sa.Integer(), sa.ForeignKey("bis_sets.id"), nullable=False),
        sa.UniqueConstraint(
            "character_id", "raid_tier_id", name="uq_character_bis_selections_character_tier"
        ),
    )
    op.create_table(
        "weekly_hierarchy_snapshot_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reclear_week_id", sa.Integer(), sa.ForeignKey("split_weeks.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("job_abbreviation", sa.String(10), nullable=False),
        sa.CheckConstraint("position > 0", name="positive_position"),
        sa.UniqueConstraint("reclear_week_id", "job_id", name="uq_weekly_snapshot_week_job"),
        sa.UniqueConstraint("reclear_week_id", "position", name="uq_weekly_snapshot_week_position"),
    )

    with op.batch_alter_table("loot_assignments") as batch:
        batch.add_column(sa.Column("intended_character_id", sa.Integer()))
        batch.add_column(sa.Column("intended_bis_set_item_id", sa.Integer()))
        batch.add_column(sa.Column("intended_final_item_id", sa.Integer()))
        batch.add_column(sa.Column("suggested_recipient_id", sa.Integer()))
        batch.add_column(sa.Column("final_recipient_id", sa.Integer()))
        batch.add_column(sa.Column("backup_recipient_id", sa.Integer()))
        batch.add_column(
            sa.Column("expected_drop_instance", sa.Integer(), server_default="1", nullable=False)
        )
        batch.add_column(sa.Column("planning_reason", sa.Text()))
        batch.add_column(
            sa.Column(
                "manually_overridden", sa.Boolean(), server_default=sa.false(), nullable=False
            )
        )
    op.execute("UPDATE loot_assignments SET intended_character_id = character_id")
    op.execute(
        "UPDATE loot_assignments SET state = CASE state WHEN 'RECEIVED' THEN 'RECEIVED' "
        "WHEN 'CANCELLED' THEN 'CANCELLED' ELSE 'PROPOSED' END"
    )
    with op.batch_alter_table("loot_assignments") as batch:
        batch.alter_column("state", existing_type=sa.String(9), type_=ASSIGNMENT_STATE)
        batch.drop_constraint("fk_loot_assignments_character_id_characters", type_="foreignkey")
        batch.drop_column("character_id")
        for column, target in (
            ("intended_character_id", "characters"),
            ("suggested_recipient_id", "characters"),
            ("final_recipient_id", "characters"),
            ("backup_recipient_id", "characters"),
            ("intended_bis_set_item_id", "bis_set_items"),
            ("intended_final_item_id", "items"),
        ):
            batch.create_foreign_key(
                f"fk_loot_assignments_{column}_{target}", target, [column], ["id"]
            )
        batch.create_check_constraint("positive_drop_instance", "expected_drop_instance > 0")

    op.create_table(
        "loot_confirmations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "loot_assignment_id", sa.Integer(), sa.ForeignKey("loot_assignments.id"), nullable=False
        ),
        sa.Column("confirmation_type", CONFIRMATION_TYPE, nullable=False),
        sa.Column("result", sa.Boolean(), nullable=False),
        sa.Column("answered_by_discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "answered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("note", sa.Text()),
    )
    op.create_table(
        "distribution_errors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reclear_week_id", sa.Integer(), sa.ForeignKey("split_weeks.id"), nullable=False),
        sa.Column(
            "loot_assignment_id", sa.Integer(), sa.ForeignKey("loot_assignments.id"), nullable=False
        ),
        sa.Column("intended_recipient_id", sa.Integer(), sa.ForeignKey("characters.id")),
        sa.Column("actual_recipient_id", sa.Integer(), sa.ForeignKey("characters.id")),
        sa.Column("error_type", ERROR_TYPE, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reported_by_discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "reported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("resolution_note", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("distribution_errors")
    op.drop_table("loot_confirmations")
    with op.batch_alter_table("loot_assignments") as batch:
        batch.add_column(sa.Column("character_id", sa.Integer()))
    op.execute("UPDATE loot_assignments SET character_id = intended_character_id")
    with op.batch_alter_table("loot_assignments") as batch:
        batch.drop_constraint("positive_drop_instance", type_="check")
        batch.alter_column("character_id", nullable=False)
        batch.create_foreign_key(
            "fk_loot_assignments_character_id_characters", "characters", ["character_id"], ["id"]
        )
        for name in (
            "intended_character_id",
            "intended_bis_set_item_id",
            "intended_final_item_id",
            "suggested_recipient_id",
            "final_recipient_id",
            "backup_recipient_id",
            "expected_drop_instance",
            "planning_reason",
            "manually_overridden",
        ):
            batch.drop_column(name)
    op.execute(
        "UPDATE loot_assignments SET state = CASE state WHEN 'RECEIVED' THEN 'RECEIVED' "
        "WHEN 'CANCELLED' THEN 'CANCELLED' ELSE 'PLANNED' END"
    )
    with op.batch_alter_table("loot_assignments") as batch:
        batch.alter_column(
            "state",
            existing_type=ASSIGNMENT_STATE,
            type_=sa.Enum(
                "PLANNED",
                "OFFERED",
                "ASSIGNED",
                "RECEIVED",
                "SKIPPED",
                "CANCELLED",
                name="lootassignmentstate",
            ),
        )
    op.drop_table("weekly_hierarchy_snapshot_entries")
    op.drop_table("character_bis_selections")
    with op.batch_alter_table("split_weeks") as batch:
        for name in (
            "raid_tier_id",
            "hierarchy_id",
            "clear_mode",
            "workflow_state",
            "created_at",
            "finalized_at",
            "notes",
        ):
            batch.drop_column(name)
    op.execute("DELETE FROM raid_tiers WHERE code = 'LEGACY_MIGRATED'")
    op.drop_table("job_hierarchy_entries")
    op.drop_table("job_hierarchies")
    with op.batch_alter_table("character_floor_book_balances") as batch:
        batch.add_column(sa.Column("quantity", sa.Integer(), server_default="0", nullable=False))
    op.execute("UPDATE character_floor_book_balances SET quantity = earned")
    with op.batch_alter_table("character_floor_book_balances") as batch:
        batch.drop_constraint("nonnegative_earned", type_="check")
        batch.drop_constraint("nonnegative_spent", type_="check")
        for name in ("earned", "spent", "manual_adjustment"):
            batch.drop_column(name)
        batch.create_check_constraint("nonnegative_quantity", "quantity >= 0")
    with op.batch_alter_table("character_gear_slots") as batch:
        for name in ("manually_complete", "note", "updated_at"):
            batch.drop_column(name)
    with op.batch_alter_table("bis_set_items") as batch:
        batch.drop_constraint("nonnegative_tome_cost", type_="check")
        batch.drop_constraint("nonnegative_book_cost", type_="check")
        for name in (
            "raid_floor_id",
            "loot_type_id",
            "base_tome_item_id",
            "tome_cost",
            "augmentation_material_type_id",
            "book_cost",
            "notes",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("bis_sets") as batch:
        for name in ("gear_set_url", "description", "active"):
            batch.drop_column(name)
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("role")
    with op.batch_alter_table("statics") as batch:
        batch.drop_column("active_raid_tier_id")
