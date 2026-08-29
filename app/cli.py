"""Database lifecycle and offline production validation commands."""

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.config import Settings, get_settings
from app.database import create_database_engine, create_session_factory
from app.models import GearSlot, Job, Static
from app.services.hierarchy import bootstrap_default_hierarchies
from bot.services.migrations import verify_migration_head

ROOT = Path(__file__).parents[1]


def alembic_ini() -> Path:
    """Resolve Alembic configuration in a checkout or installed deployment."""
    candidates = (Path.cwd() / "alembic.ini", ROOT / "alembic.ini")
    return next((path for path in candidates if path.is_file()), candidates[0])


def _alembic_config(settings: Settings) -> Config:
    config = Config(str(alembic_ini()))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def db_upgrade() -> None:
    settings = get_settings().validate(require_token=False)
    command.upgrade(_alembic_config(settings), "head")
    engine = create_database_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            bootstrap_default_hierarchies(session)
            session.commit()
    finally:
        engine.dispose()
    print("Database upgraded to the current migration head.")


def db_check() -> None:
    settings = get_settings().validate(require_token=False)
    engine = create_database_engine(settings.database_url)
    try:
        head = verify_migration_head(engine)
    finally:
        engine.dispose()
    print(f"Database migration is current: {head}")


def backup_sqlite(database_url: str, destination: Path | None = None) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError(
            "Built-in backup supports SQLite only. Use pg_dump and PostgreSQL restore tooling."
        )
    if not url.database or url.database == ":memory:":
        raise ValueError("An in-memory SQLite database cannot be backed up.")
    source = Path(url.database).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"SQLite database does not exist: {source}")
    if destination is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = source.with_name(f"{source.stem}-{stamp}.backup{source.suffix}")
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Backup already exists and will not be overwritten: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
    return destination


def db_backup() -> None:
    settings = get_settings().validate(require_token=False)
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result = backup_sqlite(settings.database_url, destination)
    print(f"SQLite backup created: {result}")


def validate_installation(settings: Settings | None = None) -> list[str]:
    settings = (settings or get_settings()).validate(require_token=True)
    messages = ["Configuration: valid", "Discord token: present (not displayed)"]
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database != ":memory:":
        directory = Path(url.database or "static_loot.db").expanduser().resolve().parent
        if not directory.exists() or not directory.is_dir():
            raise ValueError(f"SQLite directory does not exist: {directory}")
        probe = directory / ".static-loot-write-check"
        try:
            probe.touch(exist_ok=False)
            probe.unlink()
        except OSError as error:
            raise ValueError(f"SQLite directory is not writable: {directory}") from error
        messages.append(f"SQLite directory: writable ({directory})")
    engine = create_database_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        messages.append("Database connectivity: valid")
        messages.append(f"Migration head: {verify_migration_head(engine)}")
        with create_session_factory(engine)() as session:
            jobs = session.scalar(select(func.count()).select_from(Job)) or 0
            slots = session.scalar(select(func.count()).select_from(GearSlot)) or 0
            if jobs < 21 or slots < 12:
                raise ValueError("Seed records are missing; run `/setup seed`.")
            messages.append(f"Seed records: valid ({jobs} jobs, {slots} slots)")
            statics = list(session.scalars(select(Static).where(Static.active.is_(True))))
            ready = sum(
                any(hierarchy.active for hierarchy in static.job_hierarchies) for static in statics
            )
            messages.append(f"Static readiness: {ready}/{len(statics)} active statics ready")
    finally:
        engine.dispose()
    return messages


def validate() -> None:
    for message in validate_installation():
        print(message)
