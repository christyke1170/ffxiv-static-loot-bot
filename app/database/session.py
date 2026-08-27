"""Engine and session factory construction."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def create_database_engine(database_url: str | None = None, *, echo: bool = False) -> Engine:
    """Create an engine with safe defaults for SQLite and PostgreSQL."""
    url = database_url or get_settings().database_url
    connect_args = {"timeout": 30} if url.startswith("sqlite") else {}
    engine = create_engine(url, echo=echo, pool_pre_ping=True, connect_args=connect_args)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            database = engine.url.database
            if database and database != ":memory:" and Path(database).suffix:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a SQLAlchemy 2.x session factory."""
    return sessionmaker(bind=engine, expire_on_commit=False)
