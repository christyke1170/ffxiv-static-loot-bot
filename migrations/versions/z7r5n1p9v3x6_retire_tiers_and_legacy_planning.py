"""Destructively retire tier configuration and the pre-V2 planning graph.

All rows in the removed graph are disposable application data.  This revision
does not archive, translate, or backfill them.  The V2 plan, confirmation,
correction, neutral-resource, and neutral-week tables remain in place.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "z7r5n1p9v3x6"
down_revision: str | Sequence[str] | None = "y6s0n4l8q2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_TABLES = (
    "loot_receipts",
    "distribution_errors",
    "loot_confirmations",
    "loot_assignment_completion_items",
    "confirmed_reclear_material_grants",
    "loot_assignments",
    "loot_plan_participants",
    "loot_plan_runs",
    "loot_plans",
    "reclear_floor_completions",
    "character_bis_selections",
    "character_floor_book_balances",
    "character_augmentation_inventory",
    "inventory_items",
    "floor_loot_rules",
    "raid_floors",
    "loot_types",
    "augmentation_material_types",
    "raid_tiers",
    "priority_rules",
)


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        # These columns are the final tier/legacy identities on retained
        # neutral tables.  Batch mode also supports SQLite table rebuilds.
        with op.batch_alter_table("statics", recreate="auto") as batch:
            if "active_raid_tier_id" in {
                c["name"] for c in sa.inspect(bind).get_columns("statics")
            }:
                batch.drop_column("active_raid_tier_id")

        if bind.dialect.name == "sqlite":
            # Do not use batch reflection here.  At this point the historical
            # tier tables may already be absent, while split_weeks still has a
            # stale raid_tier_id foreign key in SQLite's catalog.  SQLAlchemy's
            # reflector follows that FK and raises NoSuchTableError for
            # raid_tiers before it can drop the column.
            columns = {column["name"] for column in sa.inspect(bind).get_columns("split_weeks")}
            if "raid_tier_id" in columns:
                bind.exec_driver_sql("DROP TABLE IF EXISTS _split_weeks_neutral")
                bind.exec_driver_sql(
                    "CREATE TABLE _split_weeks_neutral ("
                    "id INTEGER NOT NULL PRIMARY KEY, static_id INTEGER NOT NULL, "
                    "hierarchy_id INTEGER, week_start DATE NOT NULL, "
                    "clear_mode VARCHAR(20) NOT NULL, workflow_state VARCHAR(30) NOT NULL, "
                    "created_at DATETIME NOT NULL, finalized_at DATETIME, notes TEXT, "
                    "CONSTRAINT uq_split_weeks_static_id UNIQUE (static_id, week_start), "
                    "CONSTRAINT fk_split_weeks_static FOREIGN KEY(static_id) REFERENCES statics(id), "
                    "CONSTRAINT fk_split_weeks_hierarchy FOREIGN KEY(hierarchy_id) REFERENCES job_hierarchies(id))"
                )
                bind.exec_driver_sql(
                    "INSERT INTO _split_weeks_neutral "
                    "(id, static_id, hierarchy_id, week_start, clear_mode, workflow_state, "
                    "created_at, finalized_at, notes) "
                    "SELECT id, static_id, hierarchy_id, week_start, clear_mode, workflow_state, "
                    "created_at, finalized_at, notes FROM split_weeks"
                )
                bind.exec_driver_sql("DROP TABLE split_weeks")
                bind.exec_driver_sql("ALTER TABLE _split_weeks_neutral RENAME TO split_weeks")
        else:
            with op.batch_alter_table("split_weeks", recreate="auto") as batch:
                if "raid_tier_id" in {
                    c["name"] for c in sa.inspect(bind).get_columns("split_weeks")
                }:
                    batch.drop_column("raid_tier_id")

        # Avoid reflecting the obsolete cross-column check from w6r0t4y8u2i5;
        # SQLite would otherwise compile that check into the replacement table
        # even though raid_floor_id is being removed.
        if bind.dialect.name == "sqlite":
            bind.exec_driver_sql("DROP INDEX IF EXISTS uq_weekly_lockout_neutral_floor")
            bind.exec_driver_sql("DROP TABLE IF EXISTS _weekly_lockouts_neutral")
            bind.exec_driver_sql(
                "CREATE TABLE _weekly_lockouts_neutral ("
                "id INTEGER NOT NULL PRIMARY KEY, character_id INTEGER NOT NULL, "
                "floor_number INTEGER NOT NULL, week_start DATE NOT NULL, "
                "cleared BOOLEAN NOT NULL, loot_eligible BOOLEAN NOT NULL, "
                "CONSTRAINT uq_weekly_lockouts_neutral UNIQUE (character_id, floor_number, week_start), "
                "CONSTRAINT fk_weekly_lockouts_neutral_character FOREIGN KEY(character_id) REFERENCES characters(id))"
            )
            bind.exec_driver_sql(
                "INSERT INTO _weekly_lockouts_neutral "
                "(id, character_id, floor_number, week_start, cleared, loot_eligible) "
                "SELECT id, character_id, floor_number, week_start, cleared, loot_eligible "
                "FROM weekly_lockouts"
            )
            bind.exec_driver_sql("DROP TABLE weekly_lockouts")
            bind.exec_driver_sql("ALTER TABLE _weekly_lockouts_neutral RENAME TO weekly_lockouts")
            bind.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_weekly_lockout_neutral_floor ON weekly_lockouts "
                "(character_id, floor_number, week_start)"
            )
        else:
            with op.batch_alter_table("weekly_lockouts") as batch:
                batch.drop_constraint("weekly_lockout_exactly_one_floor_identity", type_="check")
                batch.drop_constraint("valid_weekly_floor_number", type_="check")
                batch.drop_column("raid_floor_id")
                batch.alter_column("floor_number", nullable=False)
                batch.create_unique_constraint(
                    "uq_weekly_lockouts_neutral", ["character_id", "floor_number", "week_start"]
                )

        if bind.dialect.name == "sqlite":
            bind.exec_driver_sql("DROP TABLE IF EXISTS _bis_sets_neutral")
            bind.exec_driver_sql(
                "CREATE TABLE _bis_sets_neutral ("
                "id INTEGER NOT NULL PRIMARY KEY, job_id INTEGER NOT NULL, static_id INTEGER, "
                "name VARCHAR(100) NOT NULL, gcd_label VARCHAR(30), gear_set_url VARCHAR(500), "
                "description TEXT, active BOOLEAN NOT NULL, "
                "CONSTRAINT uq_bis_sets_static_id UNIQUE (static_id, job_id), "
                "CONSTRAINT fk_bis_sets_neutral_job FOREIGN KEY(job_id) REFERENCES jobs(id), "
                "CONSTRAINT fk_bis_sets_neutral_static FOREIGN KEY(static_id) REFERENCES statics(id))"
            )
            bind.exec_driver_sql(
                "INSERT INTO _bis_sets_neutral "
                "(id, job_id, static_id, name, gcd_label, gear_set_url, description, active) "
                "SELECT id, job_id, static_id, name, gcd_label, gear_set_url, description, active "
                "FROM bis_sets"
            )
            bind.exec_driver_sql("DROP TABLE bis_sets")
            bind.exec_driver_sql("ALTER TABLE _bis_sets_neutral RENAME TO bis_sets")
        else:
            bis_set_columns = {
                column["name"] for column in sa.inspect(bind).get_columns("bis_sets")
            }
            if "raid_tier_id" in bis_set_columns:
                with op.batch_alter_table("bis_sets") as batch:
                    for foreign_key in sa.inspect(bind).get_foreign_keys("bis_sets"):
                        if "raid_tier_id" in foreign_key.get("constrained_columns", ()):
                            batch.drop_constraint(foreign_key["name"], type_="foreignkey")
                    batch.drop_column("raid_tier_id")

        if bind.dialect.name == "sqlite":
            bind.exec_driver_sql("DROP TABLE IF EXISTS _bis_set_items_neutral")
            bind.exec_driver_sql(
                "CREATE TABLE _bis_set_items_neutral ("
                "id INTEGER NOT NULL PRIMARY KEY, bis_set_id INTEGER NOT NULL, "
                "gear_slot_id INTEGER NOT NULL, classification VARCHAR(30) NOT NULL, "
                "notes TEXT, CONSTRAINT uq_bis_set_items_neutral UNIQUE (bis_set_id, gear_slot_id), "
                "CONSTRAINT fk_bis_set_items_neutral_set FOREIGN KEY(bis_set_id) REFERENCES bis_sets(id), "
                "CONSTRAINT fk_bis_set_items_neutral_slot FOREIGN KEY(gear_slot_id) REFERENCES gear_slots(id))"
            )
            bind.exec_driver_sql(
                "INSERT INTO _bis_set_items_neutral "
                "(id, bis_set_id, gear_slot_id, classification, notes) "
                "SELECT id, bis_set_id, gear_slot_id, classification, notes FROM bis_set_items"
            )
            bind.exec_driver_sql("DROP TABLE bis_set_items")
            bind.exec_driver_sql("ALTER TABLE _bis_set_items_neutral RENAME TO bis_set_items")
        else:
            with op.batch_alter_table("bis_set_items") as batch:
                batch.drop_constraint("uq_bis_set_items_bis_set_id", type_="unique")
                batch.create_unique_constraint(
                    "uq_bis_set_items_neutral", ["bis_set_id", "gear_slot_id"]
                )
                for constraint in ("nonnegative_tome_cost", "nonnegative_book_cost"):
                    batch.drop_constraint(constraint, type_="check")
                for column in (
                    "raid_floor_id",
                    "loot_type_id",
                    "augmentation_material_type_id",
                    "tome_cost",
                    "book_cost",
                ):
                    batch.drop_column(column)

            with op.batch_alter_table("bis_sets") as batch:
                batch.drop_index("uq_bis_sets_active_static_job")
                batch.create_unique_constraint("uq_bis_sets_static_id", ["static_id", "job_id"])

        # Drop the dependency leaves only after all retained tables have been
        # rebuilt without foreign keys to the legacy graph.  This ordering is
        # required by SQLite's batch reflection implementation.
        existing = _tables()
        for table in _LEGACY_TABLES:
            if table in existing:
                op.drop_table(table)
    finally:
        if bind.dialect.name == "sqlite":
            bind.execute(sa.text("PRAGMA foreign_keys=ON"))


def downgrade() -> None:
    # Deleted rows cannot be restored.  The destructive contract deliberately
    # leaves the retired schema absent on downgrade.
    pass
