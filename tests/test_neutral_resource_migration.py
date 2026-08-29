"""Dedicated migration/backfill coverage for neutral current resources."""

import sqlite3

import pytest
from alembic import command
from sqlalchemy import create_engine

from app.cli import _alembic_config
from app.config import Settings
from migrations.versions.v5q9s3u7w1y4_neutral_current_resource_balances import _backfill


def _upgrade_to_neutral(database):
    config = _alembic_config(Settings(database_url=f"sqlite:///{database}"))
    command.upgrade(config, "v5q9s3u7w1y4")
    return config


def _seed_reference_rows(database):
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "\n            INSERT INTO discord_guilds (discord_guild_id, name) VALUES (7001, 'Migration Guild');\n            INSERT INTO statics (guild_id, name, active) VALUES (1, 'Migration Static', 1);\n            INSERT INTO static_members (static_id, discord_user_id, display_name, active)\n                VALUES (1, 1, 'Migration Player', 1);\n            INSERT INTO jobs (abbreviation, name, role, uses_offhand)\n                VALUES ('MIG', 'Migration Job', 'Test', 0);\n            INSERT INTO characters (static_member_id, job_id, name, world, kind, active)\n                VALUES (1, 1, 'Migration Character', 'World', 'MAIN', 1);\n            INSERT INTO raid_tiers (code, name, active) VALUES ('MIG', 'Migration Tier', 1);\n            INSERT INTO raid_floors (raid_tier_id, floor_number, name) VALUES\n                (1, 1, 'One'), (1, 2, 'Two'), (1, 3, 'Three'), (1, 4, 'Four');\n            INSERT INTO loot_types (raid_tier_id, code, name, category) VALUES\n                (1, 'ACCESSORY_COFFER', 'Accessory', 'COFFER'),\n                (1, 'HEAD_COFFER', 'Head', 'COFFER'),\n                (1, 'GLOVES_COFFER', 'Gloves', 'COFFER'),\n                (1, 'BOOTS_COFFER', 'Boots', 'COFFER'),\n                (1, 'CHEST_COFFER', 'Chest', 'COFFER'),\n                (1, 'PANTS_COFFER', 'Pants', 'COFFER'),\n                (1, 'WEAPON_COFFER', 'Weapon', 'COFFER');\n            INSERT INTO augmentation_material_types (raid_tier_id, code, name)\n                VALUES (1, 'GLAZE', 'Glaze'), (1, 'TWINE', 'Twine');\n            "  # noqa: E501
        )
        for floor_id, earned in enumerate((2, 3, 4, 5), 1):
            connection.execute(
                "INSERT INTO character_floor_book_balances (character_id, raid_floor_id, earned, spent, manual_adjustment) VALUES (1, ?, ?, 0, 0)",  # noqa: E501
                (floor_id, earned),
            )
        connection.execute(
            "INSERT INTO character_augmentation_inventory (character_id, augmentation_material_type_id, quantity) VALUES (1, 1, 6), (1, 2, 7)"  # noqa: E501
        )
        connection.execute(
            "INSERT INTO inventory_items (character_id, loot_type_id, quantity) SELECT 1, id, 2 FROM loot_types"  # noqa: E501
        )
        connection.commit()


def test_backfill_maps_books_materials_and_all_coffers_without_changing_legacy_rows(tmp_path):
    database = tmp_path / "neutral-backfill.db"
    config = _alembic_config(Settings(database_url=f"sqlite:///{database}"))
    command.upgrade(config, "u4p8r2t6v0x3")
    _seed_reference_rows(database)
    command.upgrade(config, "v5q9s3u7w1y4")
    with sqlite3.connect(database) as connection:
        values = dict(
            connection.execute(
                "SELECT resource_key, quantity FROM v2_resource_balances WHERE static_id = 1"
            ).fetchall()
        )
        assert {f"BOOK_FLOOR_{n}": n + 1 for n in range(1, 5)}.items() <= values.items()
        assert values["ACCESSORY_GLAZE"] == 6
        assert values["ARMOR_TWINE"] == 7
        assert all(
            values[key] == 2
            for key in {
                "ACCESSORY_COFFER",
                "HEAD_COFFER",
                "GLOVES_COFFER",
                "BOOTS_COFFER",
                "CHEST_COFFER",
                "PANTS_COFFER",
                "WEAPON_COFFER",
            }
        )
        assert connection.execute(
            "SELECT earned, spent, manual_adjustment FROM character_floor_book_balances WHERE character_id = 1 AND raid_floor_id = 1"  # noqa: E501
        ).fetchone() == (2, 0, 0)


def test_backfill_conflicts_are_reported_and_existing_neutral_wins(tmp_path):
    database = tmp_path / "neutral-conflict.db"
    config = _alembic_config(Settings(database_url=f"sqlite:///{database}"))
    command.upgrade(config, "u4p8r2t6v0x3")
    _seed_reference_rows(database)
    command.upgrade(config, "v5q9s3u7w1y4")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO raid_tiers (code, name, active) VALUES ('ALIAS', 'Alias', 1)"
        )
        connection.execute(
            "INSERT INTO augmentation_material_types (raid_tier_id, code, name) VALUES (2, 'ACCESSORY_GLAZE', 'Alias Glaze')"  # noqa: E501
        )
        connection.execute(
            "INSERT INTO character_augmentation_inventory (character_id, augmentation_material_type_id, quantity) VALUES (1, 3, 4)"  # noqa: E501
        )
        connection.execute(
            "INSERT INTO augmentation_material_types (raid_tier_id, code, name) VALUES (2, 'UNKNOWN_MATERIAL', 'Unknown')"  # noqa: E501
        )
        connection.execute(
            "INSERT INTO character_augmentation_inventory (character_id, augmentation_material_type_id, quantity) VALUES (1, 4, 1)"  # noqa: E501
        )
        connection.commit()
        connection.execute(
            "UPDATE v2_resource_balances SET quantity = 99 WHERE static_id = 1 AND recipient_id = 1 AND resource_key = 'BOOK_FLOOR_1'"  # noqa: E501
        )
        connection.commit()
    with create_engine(f"sqlite:///{database}").begin() as connection:
        _backfill(connection)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT quantity FROM v2_resource_balances WHERE static_id = 1 AND resource_key = 'BOOK_FLOOR_1'"  # noqa: E501
        ).fetchone() == (99,)
        assert connection.execute(
            "SELECT COUNT(*) FROM neutral_resource_migration_issues"
        ).fetchone() == (2,)


def test_downgrade_refuses_to_discard_current_neutral_rows(tmp_path):
    database = tmp_path / "neutral-unsafe-downgrade.db"
    config = _alembic_config(Settings(database_url=f"sqlite:///{database}"))
    command.upgrade(config, "u4p8r2t6v0x3")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO statics (guild_id, name, active) VALUES (1, 'Static', 1)")
        connection.execute(
            "INSERT INTO characters (static_member_id, job_id, name, world, kind, active) VALUES (1, 1, 'Character', 'World', 'MAIN', 1)"  # noqa: E501
        )
        connection.commit()
    command.upgrade(config, "v5q9s3u7w1y4")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO v2_resource_balances (static_id, recipient_id, resource_key, quantity) VALUES (1, 1, 'ARMOR_TWINE', 1)"  # noqa: E501
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        command.downgrade(config, "u4p8r2t6v0x3")
