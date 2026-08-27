"""Shared database fixtures."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers model metadata
from app.database import Base, create_database_engine, create_session_factory


@pytest.fixture
def engine() -> Iterator[Engine]:
    database_engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(database_engine)
    yield database_engine
    database_engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = create_session_factory(engine)
    with factory() as database_session:
        yield database_session
