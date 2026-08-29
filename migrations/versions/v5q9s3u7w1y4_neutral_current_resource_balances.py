"""Add neutral current resource scope and backfill legacy balances.

Legacy resource rows are intentionally retained.  Ambiguous mappings are
recorded in ``neutral_resource_migration_issues`` and are not guessed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v5q9s3u7w1y4"
down_revision: str | Sequence[str] | None = "u4p8r2t6v0x3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEYS = {
    *(f"BOOK_FLOOR_{number}" for number in range(1, 5)),
    "ACCESSORY_GLAZE",
    "ARMOR_TWINE",
    "ACCESSORY_COFFER",
    "HEAD_COFFER",
    "GLOVES_COFFER",
    "BOOTS_COFFER",
    "CHEST_COFFER",
    "PANTS_COFFER",
    "WEAPON_COFFER",
    "WEAPON_TOMESTONE",
    "WEAPON_AUGMENT",
}


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table("v2_resource_balances") as batch:
            batch.add_column(sa.Column("static_id", sa.Integer(), nullable=True))
            batch.alter_column(
                "plan_id", existing_type=sa.Integer(), existing_nullable=False, nullable=True
            )
            batch.create_foreign_key(
                "fk_v2_resource_balances_static", "statics", ["static_id"], ["id"]
            )
            batch.create_check_constraint(
                "v2_resource_balance_scope",
                "(plan_id IS NOT NULL AND static_id IS NULL) OR "
                "(plan_id IS NULL AND static_id IS NOT NULL)",
            )
            batch.create_check_constraint(
                "supported_v2_resource_key",
                "resource_key IN ('BOOK_FLOOR_1','BOOK_FLOOR_2','BOOK_FLOOR_3','BOOK_FLOOR_4',"
                "'ACCESSORY_GLAZE','ARMOR_TWINE','ACCESSORY_COFFER','HEAD_COFFER','GLOVES_COFFER',"
                "'BOOTS_COFFER','CHEST_COFFER','PANTS_COFFER','WEAPON_COFFER','WEAPON_TOMESTONE',"
                "'WEAPON_AUGMENT')",
            )
    else:
        op.add_column("v2_resource_balances", sa.Column("static_id", sa.Integer(), nullable=True))
        op.alter_column(
            "v2_resource_balances", "plan_id", existing_type=sa.Integer(), nullable=True
        )

        op.create_foreign_key(
            "fk_v2_resource_balances_static",
            "v2_resource_balances",
            "statics",
            ["static_id"],
            ["id"],
        )
        op.create_check_constraint(
            "v2_resource_balance_scope",
            "v2_resource_balances",
            "(plan_id IS NOT NULL AND static_id IS NULL) OR "
            "(plan_id IS NULL AND static_id IS NOT NULL)",
        )
        op.create_check_constraint(
            "supported_v2_resource_key",
            "v2_resource_balances",
            "resource_key IN ('BOOK_FLOOR_1','BOOK_FLOOR_2','BOOK_FLOOR_3','BOOK_FLOOR_4',"
            "'ACCESSORY_GLAZE','ARMOR_TWINE','ACCESSORY_COFFER','HEAD_COFFER','GLOVES_COFFER',"
            "'BOOTS_COFFER','CHEST_COFFER','PANTS_COFFER','WEAPON_COFFER','WEAPON_TOMESTONE',"
            "'WEAPON_AUGMENT')",
        )
    op.create_table(
        "neutral_resource_migration_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False),
        sa.Column("resource_key", sa.String(50), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
    )
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_v2_current_resource_balance ON v2_resource_balances "
            "(static_id, recipient_id, resource_key) WHERE static_id IS NOT NULL"
        )
    else:
        op.create_index(
            "uq_v2_current_resource_balance",
            "v2_resource_balances",
            ["static_id", "recipient_id", "resource_key"],
            unique=True,
            postgresql_where=sa.text("static_id IS NOT NULL"),
        )
    _backfill(connection)


def downgrade() -> None:
    connection = op.get_bind()
    current_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM v2_resource_balances WHERE static_id IS NOT NULL")
    ).scalar_one()
    if current_count:
        raise RuntimeError(
            "Refusing to downgrade neutral current resources while "
            f"{current_count} current balance row(s) exist. Archive or remove them explicitly first."
        )
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("DROP INDEX IF EXISTS uq_v2_current_resource_balance")
        with op.batch_alter_table("v2_resource_balances") as batch:
            batch.drop_constraint("supported_v2_resource_key", type_="check")
            batch.drop_constraint("v2_resource_balance_scope", type_="check")
            batch.drop_constraint("fk_v2_resource_balances_static", type_="foreignkey")
            batch.drop_column("static_id")
            batch.alter_column(
                "plan_id", existing_type=sa.Integer(), existing_nullable=True, nullable=False
            )
    else:
        op.drop_constraint("supported_v2_resource_key", "v2_resource_balances", type_="check")
        op.drop_constraint("v2_resource_balance_scope", "v2_resource_balances", type_="check")
        op.drop_index("uq_v2_current_resource_balance", table_name="v2_resource_balances")
        op.drop_constraint(
            "fk_v2_resource_balances_static", "v2_resource_balances", type_="foreignkey"
        )
        op.drop_column("v2_resource_balances", "static_id")
        op.alter_column(
            "v2_resource_balances", "plan_id", existing_type=sa.Integer(), nullable=False
        )
    op.drop_table("neutral_resource_migration_issues")


def _backfill(connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT c.id, sm.static_id, 'BOOK_FLOOR_' || rf.floor_number, "
            "MAX(cfb.earned - cfb.spent + cfb.manual_adjustment), COUNT(*) "
            "FROM character_floor_book_balances cfb "
            "JOIN characters c ON c.id = cfb.character_id "
            "JOIN static_members sm ON sm.id = c.static_member_id "
            "JOIN raid_floors rf ON rf.id = cfb.raid_floor_id "
            "WHERE rf.floor_number BETWEEN 1 AND 4 GROUP BY c.id, sm.static_id, rf.floor_number"
        )
    ).fetchall()
    rows += connection.execute(
        sa.text(
            "SELECT c.id, sm.static_id, CASE UPPER(amt.code) "
            "WHEN 'GLAZE' THEN 'ACCESSORY_GLAZE' WHEN 'TWINE' THEN 'ARMOR_TWINE' "
            "WHEN 'ACCESSORY_GLAZE' THEN 'ACCESSORY_GLAZE' WHEN 'ARMOR_TWINE' THEN 'ARMOR_TWINE' END, "
            "MAX(cai.quantity), COUNT(*) FROM character_augmentation_inventory cai "
            "JOIN characters c ON c.id = cai.character_id JOIN static_members sm ON sm.id = c.static_member_id "
            "JOIN augmentation_material_types amt ON amt.id = cai.augmentation_material_type_id "
            "WHERE UPPER(amt.code) IN ('GLAZE','TWINE','ACCESSORY_GLAZE','ARMOR_TWINE') "
            "GROUP BY c.id, sm.static_id, CASE UPPER(amt.code) "
            "WHEN 'GLAZE' THEN 'ACCESSORY_GLAZE' WHEN 'TWINE' THEN 'ARMOR_TWINE' "
            "WHEN 'ACCESSORY_GLAZE' THEN 'ACCESSORY_GLAZE' WHEN 'ARMOR_TWINE' THEN 'ARMOR_TWINE' END"
        )
    ).fetchall()
    unknown_materials = connection.execute(
        sa.text(
            "SELECT c.id, UPPER(amt.code) FROM character_augmentation_inventory cai "
            "JOIN characters c ON c.id = cai.character_id "
            "JOIN augmentation_material_types amt ON amt.id = cai.augmentation_material_type_id "
            "WHERE UPPER(amt.code) NOT IN ('GLAZE','TWINE','ACCESSORY_GLAZE','ARMOR_TWINE')"
        )
    ).fetchall()
    unknown_loot = connection.execute(
        sa.text(
            "SELECT c.id, UPPER(lt.code) FROM inventory_items ii "
            "JOIN characters c ON c.id = ii.character_id JOIN loot_types lt ON lt.id = ii.loot_type_id "
            "WHERE UPPER(lt.code) NOT IN ('ACCESSORY_COFFER','HEAD_COFFER','GLOVES_COFFER',"
            "'BOOTS_COFFER','CHEST_COFFER','PANTS_COFFER','WEAPON_COFFER')"
        )
    ).fetchall()
    for character_id, key in (*unknown_materials, *unknown_loot):
        connection.execute(
            sa.text(
                "INSERT INTO neutral_resource_migration_issues "
                "(character_id, resource_key, details) VALUES (:character_id, :resource_key, :details)"
            ),
            {
                "character_id": character_id,
                "resource_key": key,
                "details": "Legacy resource has no supported neutral logical-key mapping.",
            },
        )
    rows += connection.execute(
        sa.text(
            "SELECT c.id, sm.static_id, UPPER(lt.code), MAX(ii.quantity), COUNT(*) "
            "FROM inventory_items ii JOIN characters c ON c.id = ii.character_id "
            "JOIN static_members sm ON sm.id = c.static_member_id JOIN loot_types lt ON lt.id = ii.loot_type_id "
            "WHERE UPPER(lt.code) IN ('ACCESSORY_COFFER','HEAD_COFFER','GLOVES_COFFER','BOOTS_COFFER',"
            "'CHEST_COFFER','PANTS_COFFER','WEAPON_COFFER') GROUP BY c.id, sm.static_id, UPPER(lt.code)"
        )
    ).fetchall()
    for character_id, static_id, key, quantity, source_count in rows:
        if key not in KEYS:
            continue
        if source_count > 1:
            connection.execute(
                sa.text(
                    "INSERT INTO neutral_resource_migration_issues "
                    "(character_id, resource_key, details) VALUES (:character_id, :resource_key, :details)"
                ),
                {
                    "character_id": character_id,
                    "resource_key": key,
                    "details": "Multiple legacy rows map to one neutral key; backfill skipped.",
                },
            )
            continue
        existing = connection.execute(
            sa.text(
                "SELECT id FROM v2_resource_balances WHERE static_id = :static_id "
                "AND recipient_id = :recipient_id AND resource_key = :resource_key"
            ),
            {"static_id": static_id, "recipient_id": character_id, "resource_key": key},
        ).scalar()
        if existing is not None:
            continue
        connection.execute(
            sa.text(
                "INSERT INTO v2_resource_balances "
                "(plan_id, static_id, recipient_id, resource_key, quantity) "
                "VALUES (NULL, :static_id, :recipient_id, :resource_key, :quantity)"
            ),
            {
                "static_id": static_id,
                "recipient_id": character_id,
                "resource_key": key,
                "quantity": max(quantity or 0, 0),
            },
        )
