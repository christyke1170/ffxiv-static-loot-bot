"""Migration checks used before Discord connects."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from bot.errors import StaleMigrationError


def alembic_ini() -> Path:
    candidates = (Path.cwd() / "alembic.ini", Path(__file__).parents[2] / "alembic.ini")
    return next((path for path in candidates if path.is_file()), candidates[0])


def migration_head() -> str:
    config = Config(str(alembic_ini()))
    return ScriptDirectory.from_config(config).get_current_head()


def verify_migration_head(engine) -> str:
    expected = migration_head()
    with engine.connect() as connection:
        inspector = inspect(connection)
        if "alembic_version" not in inspector.get_table_names():
            raise StaleMigrationError(
                "Database has no migration table; run `alembic upgrade head`."
            )
        current = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    if current != expected:
        raise StaleMigrationError(
            f"Database migration {current or 'none'} is stale; run `alembic upgrade head`."
        )
    return expected
