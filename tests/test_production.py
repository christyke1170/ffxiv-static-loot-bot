"""Production database, startup, redaction, backup, and validation tests."""

import logging
import sqlite3

import pytest
from alembic import command
from sqlalchemy import inspect

from app.cli import _alembic_config, alembic_ini, backup_sqlite, validate_installation
from app.config import Settings
from app.database import create_database_engine, create_session_factory
from app.security import RedactingFilter, redact
from app.services import seed_reference_data
from bot.client import StaticLootClient


def test_sqlite_foreign_keys_busy_timeout_and_wal(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'production.db'}")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
    finally:
        engine.dispose()


def test_safe_sqlite_backup_and_no_overwrite(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE history (value TEXT)")
        connection.execute("INSERT INTO history VALUES ('retained')")
    target = tmp_path / "backup.db"
    assert backup_sqlite(f"sqlite:///{source}", target) == target.resolve()
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT value FROM history").fetchone() == ("retained",)
    with pytest.raises(FileExistsError, match="not be overwritten"):
        backup_sqlite(f"sqlite:///{source}", target)


def test_postgresql_backup_rejected():
    with pytest.raises(ValueError, match="pg_dump"):
        backup_sqlite("postgresql+psycopg://user:password@localhost/static_loot")


def test_installed_migration_config_resolves_working_directory(monkeypatch, tmp_path):
    expected = tmp_path / "alembic.ini"
    expected.write_text("[alembic]\nscript_location = migrations\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert alembic_ini() == expected


def test_redaction_hides_urls_and_assignments():
    value = redact("postgresql://admin:hunter2@db/x token=abc password:xyz")
    assert "hunter2" not in value and "abc" not in value and "xyz" not in value
    assert "admin:***@db" in value
    record = logging.LogRecord("test", logging.ERROR, "", 1, "secret=value", (), None)
    assert RedactingFilter().filter(record) and "value" not in record.msg


def test_validation_command_checks_real_database(tmp_path):
    database = tmp_path / "validate.db"
    settings = Settings(database_url=f"sqlite:///{database}", discord_token="not-printed")
    command.upgrade(_alembic_config(settings), "head")
    engine = create_database_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            seed_reference_data(session)
            session.commit()
    finally:
        engine.dispose()
    messages = validate_installation(settings)
    assert any("Seed records: valid" in message for message in messages)
    assert all("not-printed" not in message for message in messages)


def test_clean_migration_downgrade_and_reupgrade_remove_current_gear_tier(tmp_path):
    database = tmp_path / "migration-cycle.db"
    settings = Settings(database_url=f"sqlite:///{database}")
    config = _alembic_config(settings)

    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(character_gear_slots)")}
        assert "current_raid_tier_id" not in columns

    command.downgrade(config, "g7b2c4d5e6f7")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(character_gear_slots)")}
        assert "current_raid_tier_id" in columns
        foreign_keys = list(connection.execute("PRAGMA foreign_key_list(character_gear_slots)"))
        assert any(
            row[2] == "raid_tiers" and row[3] == "current_raid_tier_id" for row in foreign_keys
        )

    command.upgrade(config, "head")
    engine = create_database_engine(settings.database_url)
    try:
        inspector = inspect(engine)
        assert "current_raid_tier_id" not in {
            column["name"] for column in inspector.get_columns("character_gear_slots")
        }
        assert all(
            "current_raid_tier_id" not in foreign_key.get("constrained_columns", ())
            for foreign_key in inspector.get_foreign_keys("character_gear_slots")
        )
    finally:
        engine.dispose()


async def test_graceful_shutdown_disposes_database(monkeypatch, tmp_path):
    client = StaticLootClient(Settings(database_url=f"sqlite:///{tmp_path / 'close.db'}"))
    disposed = False

    def dispose():
        nonlocal disposed
        disposed = True

    monkeypatch.setattr(client.database_engine, "dispose", dispose)
    monkeypatch.setattr("discord.ext.commands.Bot.close", lambda _self: _completed())
    await client.close()
    assert disposed


async def _completed():
    return None


async def test_extension_failure_unloads_partial_startup(monkeypatch, tmp_path):
    client = StaticLootClient(Settings(database_url=f"sqlite:///{tmp_path / 'extensions.db'}"))
    loaded = []
    unloaded = []

    async def load(name):
        if len(loaded) == 2:
            raise RuntimeError("extension failed")
        loaded.append(name)

    async def unload(name):
        unloaded.append(name)

    monkeypatch.setattr(client, "load_extension", load)
    monkeypatch.setattr(client, "unload_extension", unload)
    with pytest.raises(RuntimeError, match="extension failed"):
        await client.setup_hook()
    assert unloaded == list(reversed(loaded))
    await client.close()


def test_startup_migration_failure_prevents_client_creation(monkeypatch):
    created = False

    def client(_settings):
        nonlocal created
        created = True

    monkeypatch.setattr("bot.main.get_settings", lambda: Settings(discord_token="x"))
    monkeypatch.setattr(
        "bot.main.verify_migration_head",
        lambda _engine: (_ for _ in ()).throw(RuntimeError("stale")),
    )
    monkeypatch.setattr("bot.main.StaticLootClient", client)
    with pytest.raises(RuntimeError, match="stale"):
        __import__("bot.main", fromlist=["main"]).main()
    assert not created


async def test_persistent_views_register_before_sync(monkeypatch, tmp_path):
    client = StaticLootClient(Settings(database_url=f"sqlite:///{tmp_path / 'order.db'}"))
    order = []

    async def load(_name):
        return None

    monkeypatch.setattr(client, "load_extension", load)
    monkeypatch.setattr(
        "bot.views.confirmation.register_persistent_confirmation_views",
        lambda _bot: order.append("views"),
    )
    monkeypatch.setattr(client.tree, "sync", lambda **_kwargs: _record(order, "sync"))
    await client.setup_hook()
    assert order == ["views", "sync"]
    await client.close()


async def _record(values, value):
    values.append(value)
