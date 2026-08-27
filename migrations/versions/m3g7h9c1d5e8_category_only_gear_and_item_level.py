"""Correct gear to categories and add relative item-level configuration."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "m3g7h9c1d5e8"
down_revision: str | Sequence[str] | None = "l2f6g8b0c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

gear_classification = sa.Enum(
    "SAVAGE",
    "AUGMENTED_TOME",
    "TOME",
    "CRAFTED_EX",
    "GARBAGE",
    "NOT_APPLICABLE",
    name="gearclassification",
)


def upgrade() -> None:
    with op.batch_alter_table("loot_plan_runs") as batch:
        batch.drop_constraint("uq_loot_plan_runs_loot_plan_id", type_="unique")
        batch.create_unique_constraint(
            "uq_loot_plan_runs_plan_run_number", ["loot_plan_id", "run_number"]
        )
        batch.create_unique_constraint("uq_loot_plan_runs_plan_name", ["loot_plan_id", "name"])
    op.add_column("statics", sa.Column("crafted_item_level", sa.Integer()))
    op.add_column(
        "jobs",
        sa.Column("uses_offhand", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # ``items`` remains a named loot/material resource table. Its two legacy
    # metadata columns are intentionally deprecated in place because rebuilding
    # this heavily referenced table is unsafe on populated SQLite databases.
    op.execute("UPDATE jobs SET uses_offhand = 1 WHERE abbreviation = 'PLD'")

    for table, column in (
        ("character_gear_slots", "current_classification"),
        ("bis_set_items", "classification"),
        ("loot_assignment_completion_items", "previous_gear_classification"),
        ("loot_confirmations", "previous_gear_classification"),
    ):
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column} = 'CRAFTED_EX' "
                f"WHERE {column} IN ('CRAFTED', 'EX_WEAPON')"
            )
        )

    with op.batch_alter_table("loot_assignments") as batch:
        batch.add_column(sa.Column("gear_slot_id", sa.Integer()))
        batch.add_column(sa.Column("resulting_classification", gear_classification))
        batch.create_foreign_key(
            "fk_loot_assignments_gear_slot_id_gear_slots", "gear_slots", ["gear_slot_id"], ["id"]
        )
    op.execute(
        """
        UPDATE loot_assignments
        SET gear_slot_id = (SELECT gear_slot_id FROM bis_set_items
                            WHERE id = loot_assignments.intended_bis_set_item_id),
            resulting_classification = (SELECT classification FROM bis_set_items
                                        WHERE id = loot_assignments.intended_bis_set_item_id)
        WHERE intended_bis_set_item_id IS NOT NULL
        """
    )
    with op.batch_alter_table("loot_assignments") as batch:
        batch.drop_column("intended_final_item_id")

    with op.batch_alter_table("loot_assignment_completion_items") as batch:
        batch.add_column(sa.Column("resulting_classification", gear_classification))
    op.execute(
        """
        UPDATE loot_assignment_completion_items
        SET resulting_classification = COALESCE(
            (SELECT classification FROM bis_set_items
             WHERE id = loot_assignment_completion_items.bis_set_item_id), 'SAVAGE')
        """
    )
    with op.batch_alter_table("loot_assignment_completion_items") as batch:
        batch.alter_column("resulting_classification", nullable=False)
        batch.drop_column("previous_gear_item_id")
        batch.drop_column("intended_final_item_id")

    with op.batch_alter_table("loot_confirmations") as batch:
        batch.drop_column("previous_gear_item_id")
    with op.batch_alter_table("loot_receipts") as batch:
        batch.drop_column("item_id")

    op.rename_table("inventory_items", "legacy_inventory_items")
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("loot_type_id", sa.Integer(), sa.ForeignKey("loot_types.id")),
        sa.Column("gear_slot_id", sa.Integer(), sa.ForeignKey("gear_slots.id")),
        sa.Column("classification", gear_classification),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("quantity >= 0", name="nonnegative_quantity"),
        sa.CheckConstraint(
            "(loot_type_id IS NOT NULL AND gear_slot_id IS NULL AND classification IS NULL) OR "
            "(loot_type_id IS NULL AND gear_slot_id IS NOT NULL AND classification IS NOT NULL)",
            name="inventory_resource_or_category",
        ),
        sa.UniqueConstraint("character_id", "loot_type_id"),
        sa.UniqueConstraint("character_id", "gear_slot_id", "classification"),
    )
    op.execute(
        """
        INSERT INTO inventory_items (character_id, loot_type_id, quantity)
        SELECT old.character_id, loot.id, SUM(old.quantity)
        FROM legacy_inventory_items old
        JOIN loot_types loot ON loot.item_id = old.item_id
        WHERE old.quantity > 0
        GROUP BY old.character_id, loot.id
        """
    )
    op.execute(
        """
        INSERT INTO inventory_items (character_id, gear_slot_id, classification, quantity)
        SELECT old.character_id, bis.gear_slot_id,
               CASE WHEN bis.classification IN ('CRAFTED', 'EX_WEAPON')
                    THEN 'CRAFTED_EX' ELSE bis.classification END,
               SUM(old.quantity)
        FROM legacy_inventory_items old
        JOIN bis_set_items bis ON old.item_id IN (bis.desired_item_id, bis.base_tome_item_id)
        WHERE old.quantity > 0
          AND bis.classification != 'NOT_APPLICABLE'
          AND NOT EXISTS (SELECT 1 FROM loot_types loot WHERE loot.item_id = old.item_id)
        GROUP BY old.character_id, bis.gear_slot_id, bis.classification
        """
    )
    op.drop_table("legacy_inventory_items")

    with op.batch_alter_table("bis_set_items") as batch:
        batch.drop_column("base_tome_item_id")
        batch.drop_column("desired_item_id")


def downgrade() -> None:
    with op.batch_alter_table("loot_plan_runs") as batch:
        batch.drop_constraint("uq_loot_plan_runs_plan_name", type_="unique")
        batch.drop_constraint("uq_loot_plan_runs_plan_run_number", type_="unique")
        batch.create_unique_constraint("uq_loot_plan_runs_loot_plan_id", ["loot_plan_id", "name"])
    with op.batch_alter_table("bis_set_items") as batch:
        batch.add_column(sa.Column("desired_item_id", sa.Integer()))
        batch.add_column(sa.Column("base_tome_item_id", sa.Integer()))
    op.rename_table("inventory_items", "category_inventory_items")
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("character_id", "item_id"),
        sa.CheckConstraint("quantity >= 0", name="nonnegative_quantity"),
    )
    op.execute(
        """
        INSERT INTO inventory_items (character_id, item_id, quantity)
        SELECT inventory.character_id, loot.item_id, inventory.quantity
        FROM category_inventory_items inventory
        JOIN loot_types loot ON loot.id = inventory.loot_type_id
        WHERE loot.item_id IS NOT NULL
        """
    )
    op.drop_table("category_inventory_items")

    with op.batch_alter_table("loot_receipts") as batch:
        batch.add_column(sa.Column("item_id", sa.Integer()))
    with op.batch_alter_table("loot_confirmations") as batch:
        batch.add_column(sa.Column("previous_gear_item_id", sa.Integer()))
    with op.batch_alter_table("loot_assignment_completion_items") as batch:
        batch.add_column(sa.Column("intended_final_item_id", sa.Integer()))
        batch.add_column(sa.Column("previous_gear_item_id", sa.Integer()))
        batch.drop_column("resulting_classification")
    with op.batch_alter_table("loot_assignments") as batch:
        batch.add_column(sa.Column("intended_final_item_id", sa.Integer()))
        batch.drop_column("resulting_classification")
        batch.drop_column("gear_slot_id")
    op.drop_column("jobs", "uses_offhand")
    op.drop_column("statics", "crafted_item_level")
