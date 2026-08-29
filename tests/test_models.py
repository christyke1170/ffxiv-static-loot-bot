from sqlalchemy import inspect

from app.database import Base


def test_neutral_schema_contains_v2_tables(engine):
    tables = set(inspect(engine).get_table_names())
    assert {"split_weeks", "v2_plans", "v2_confirmations", "v2_resource_balances"} <= tables
    assert "raid_tiers" not in tables


def test_metadata_is_neutral():
    assert (
        "v2_corrections" in Base.metadata.tables and "character_gear_slots" in Base.metadata.tables
    )
