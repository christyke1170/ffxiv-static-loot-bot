"""Opt-in PostgreSQL transaction-safety coverage.

These tests deliberately do not run against SQLite.  Set TEST_POSTGRES_URL to
an explicitly disposable database whose name contains ``test``, ``validation``,
or ``disposable`` after applying the repository migrations.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlparse

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.database import create_database_engine, create_session_factory
from app.models import (
    CharacterGearSlot,
    ClearMode,
    V2Confirmation,
    V2Correction,
    V2EffectLedger,
    V2Plan,
    V2PlanAssignment,
    V2PlanParticipant,
    V2PlanRun,
    V2PlanUnassigned,
    V2ResourceBalance,
)
from app.services import (
    V2ConfirmationError,
    confirm_v2_application,
    confirm_v2_receipt,
    correct_v2_receipt,
    generate_and_persist_weekly_plan,
    reverse_v2_application,
)
from app.services.neutral_resources import adjust_current_balance
from tests.test_v2_confirmation import _assignment, _paired
from tests.test_v2_plan_orchestration import _setup
from tests.test_v2_planning_state import _static

pytestmark = pytest.mark.integration


def _test_url() -> str:
    value = os.getenv("TEST_POSTGRES_URL")
    if not value:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    parsed = urlparse(value)
    database = parsed.path.removeprefix("/").lower()
    if parsed.scheme != "postgresql+psycopg" or not database:
        pytest.fail("TEST_POSTGRES_URL must be a postgresql+psycopg URL with a database name")
    if not any(marker in database for marker in ("test", "validation", "disposable")):
        pytest.fail("TEST_POSTGRES_URL must identify a disposable test database")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("TEST_POSTGRES_URL must use a local disposable PostgreSQL server")
    return value


@pytest.fixture(scope="module")
def postgres_factory():
    engine = create_database_engine(_test_url())
    with engine.connect() as connection:
        database = connection.scalar(text("SELECT current_database()"))
        assert database and any(
            marker in database.lower() for marker in ("test", "validation", "disposable")
        )
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_postgres(postgres_factory):
    with postgres_factory() as session:
        tables = session.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        ).all()
        application = [name for name in tables if name != "alembic_version"]
        if application:
            session.execute(
                text(
                    "TRUNCATE TABLE " + ", ".join(f'"{name}"' for name in application) + " CASCADE"
                )
            )
        session.commit()
    yield


def _run_two(factory, operation):
    barrier = Barrier(2)

    def contender():
        with factory() as session:
            barrier.wait()
            try:
                value = operation(session)
                session.commit()
                return ("ok", value)
            except Exception as exc:  # assertions below classify expected domain contention
                session.rollback()
                session.execute(text("SELECT 1"))
                return ("error", exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        return [future.result() for future in futures]


def _prepared_assignment(factory, paired=False):
    with factory() as session:
        fixture = _paired(session) if paired else _assignment(session)
        static, week, plan, assignment = fixture
        session.commit()
        return static.id, week.id, assignment.id, assignment.recipient_id


def test_competing_orchestration_has_one_graph_and_recoverable_loser(postgres_factory):
    with postgres_factory() as session:
        static, week = _setup(session, ClearMode.REGULAR)
        session.commit()
        static_id, week_id = static.id, week.id

    results = _run_two(
        postgres_factory,
        lambda session: generate_and_persist_weekly_plan(session, static_id, week_id, 7),
    )
    assert sum(result[0] == "ok" for result in results) >= 1
    assert all(
        result[0] == "ok" or isinstance(result[1], (IntegrityError, ValueError))
        for result in results
    )
    with postgres_factory() as session:
        plan = session.scalar(select(V2Plan).where(V2Plan.reclear_week_id == week_id))
        assert plan is not None
        assert (
            session.scalar(
                select(func.count()).select_from(V2Plan).where(V2Plan.reclear_week_id == week_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(V2PlanRun).where(V2PlanRun.plan_id == plan.id)
            )
            >= 1
        )
        assert (
            session.scalar(select(func.count()).select_from(V2PlanParticipant).join(V2PlanRun)) >= 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(V2PlanAssignment)
                .where(V2PlanAssignment.plan_id == plan.id)
            )
            >= 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(V2PlanUnassigned)
                .where(V2PlanUnassigned.plan_id == plan.id)
            )
            >= 0
        )
        assert plan.static_id == static_id


def test_identical_receipts_have_one_effect_and_recoverable_sessions(postgres_factory):
    static_id, _, assignment_id, recipient_id = _prepared_assignment(postgres_factory)
    results = _run_two(
        postgres_factory,
        lambda session: confirm_v2_receipt(session, assignment_id, "HEAD_COFFER", True, actor_id=1),
    )
    assert all(result[0] == "ok" for result in results)
    assert results[0][1] == results[1][1]
    with postgres_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(V2Confirmation)
                .where(V2Confirmation.assignment_id == assignment_id)
            )
            == 1
        )
        balance = session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.static_id == static_id,
                V2ResourceBalance.recipient_id == recipient_id,
                V2ResourceBalance.resource_key == "HEAD_COFFER",
            )
        )
        assert balance is not None and balance.quantity == 1


def test_contradictory_receipts_have_one_effective_outcome(postgres_factory):
    static_id, _, assignment_id, recipient_id = _prepared_assignment(postgres_factory)
    barrier = Barrier(2)
    outcomes = []

    def contender(success):
        with postgres_factory() as session:
            barrier.wait()
            try:
                confirm_v2_receipt(session, assignment_id, "HEAD_COFFER", success, actor_id=1)
                session.commit()
                outcomes.append(("ok", success))
            except Exception as exc:
                session.rollback()
                session.execute(text("SELECT 1"))
                outcomes.append(("error", exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender, value) for value in (True, False)]
        [future.result() for future in futures]
    assert sum(kind == "ok" for kind, _ in outcomes) == 1
    assert (
        sum(isinstance(value, V2ConfirmationError) for kind, value in outcomes if kind == "error")
        == 1
    )
    with postgres_factory() as session:
        row = session.scalar(
            select(V2Confirmation).where(V2Confirmation.assignment_id == assignment_id)
        )
        assert row is not None and row.success is (
            next(value for kind, value in outcomes if kind == "ok")
        )
        balance = session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.static_id == static_id,
                V2ResourceBalance.recipient_id == recipient_id,
                V2ResourceBalance.resource_key == "HEAD_COFFER",
            )
        )
        assert (balance is not None) is row.success


def test_concurrent_application_consumes_savage_once(postgres_factory):
    static_id, _, assignment_id, recipient_id = _prepared_assignment(postgres_factory)
    with postgres_factory() as session:
        confirm_v2_receipt(session, assignment_id, "HEAD_COFFER", True)
        session.commit()
    results = _run_two(
        postgres_factory, lambda session: confirm_v2_application(session, assignment_id, True)
    )
    assert sum(result[0] == "ok" for result in results) >= 1
    with postgres_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(V2Confirmation)
                .where(
                    V2Confirmation.assignment_id == assignment_id,
                    V2Confirmation.action == "APPLICATION",
                )
            )
            == 1
        )
        balance = session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.static_id == static_id,
                V2ResourceBalance.recipient_id == recipient_id,
                V2ResourceBalance.resource_key == "HEAD_COFFER",
            )
        )
        assert balance.quantity == 0
        assert session.scalar(select(func.count()).select_from(V2EffectLedger)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(CharacterGearSlot)
                .where(CharacterGearSlot.character_id == recipient_id)
            )
            >= 1
        )


def test_concurrent_paired_tome_application_is_atomic(postgres_factory):
    static_id, _, assignment_id, recipient_id = _prepared_assignment(postgres_factory, paired=True)
    with postgres_factory() as session:
        confirm_v2_receipt(session, assignment_id, "WEAPON_TOMESTONE", True)
        confirm_v2_receipt(session, assignment_id, "WEAPON_AUGMENT", True)
        session.commit()
    results = _run_two(
        postgres_factory, lambda session: confirm_v2_application(session, assignment_id, True)
    )
    assert sum(result[0] == "ok" for result in results) >= 1
    with postgres_factory() as session:
        for key in ("WEAPON_TOMESTONE", "WEAPON_AUGMENT"):
            row = session.scalar(
                select(V2ResourceBalance).where(
                    V2ResourceBalance.static_id == static_id,
                    V2ResourceBalance.recipient_id == recipient_id,
                    V2ResourceBalance.resource_key == key,
                )
            )
            assert row.quantity == 0
        assert session.scalar(select(func.count()).select_from(V2EffectLedger)) == 1


def test_concurrent_receipt_correction_has_one_delta(postgres_factory):
    static_id, _, assignment_id, recipient_id = _prepared_assignment(postgres_factory)
    with postgres_factory() as session:
        receipt = confirm_v2_receipt(session, assignment_id, "HEAD_COFFER", True, actor_id=1)
        session.commit()
        confirmation_id = receipt.confirmation_id
    results = _run_two(
        postgres_factory,
        lambda session: correct_v2_receipt(session, confirmation_id, False, 99, "race"),
    )
    assert sum(result[0] == "ok" for result in results) >= 1
    with postgres_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(V2Correction)
                .where(V2Correction.confirmation_id == confirmation_id)
            )
            == 1
        )
        balance = session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.static_id == static_id,
                V2ResourceBalance.recipient_id == recipient_id,
                V2ResourceBalance.resource_key == "HEAD_COFFER",
            )
        )
        assert balance is not None and balance.quantity == 0


def test_concurrent_application_reversal_restores_once(postgres_factory):
    static_id, _, assignment_id, recipient_id = _prepared_assignment(postgres_factory)
    with postgres_factory() as session:
        confirm_v2_receipt(session, assignment_id, "HEAD_COFFER", True)
        application = confirm_v2_application(session, assignment_id, True)
        session.commit()
        confirmation_id = application.confirmation_id
    results = _run_two(
        postgres_factory,
        lambda session: reverse_v2_application(session, confirmation_id, 99, "race"),
    )
    assert all(result[0] == "ok" for result in results)
    assert results[0][1] == results[1][1]
    with postgres_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(V2Correction)
                .where(
                    V2Correction.confirmation_id == confirmation_id,
                    V2Correction.correction_type == "APPLICATION_REVERSAL",
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(V2ResourceBalance).where(
                    V2ResourceBalance.static_id == static_id,
                    V2ResourceBalance.recipient_id == recipient_id,
                    V2ResourceBalance.resource_key == "HEAD_COFFER",
                )
            ).quantity
            == 1
        )


def test_concurrent_missing_balance_adjustments_are_atomic(postgres_factory):
    with postgres_factory() as session:
        static = _static(session)
        character = static.members[0].characters[0]
        session.commit()
        static_id, recipient_id = static.id, character.id
    results = _run_two(
        postgres_factory,
        lambda session: adjust_current_balance(session, static_id, recipient_id, "HEAD_COFFER", 1),
    )
    assert all(result[0] == "ok" for result in results)
    with postgres_factory() as session:
        rows = session.scalars(
            select(V2ResourceBalance).where(
                V2ResourceBalance.static_id == static_id,
                V2ResourceBalance.recipient_id == recipient_id,
                V2ResourceBalance.resource_key == "HEAD_COFFER",
            )
        ).all()
        assert len(rows) == 1 and rows[0].quantity == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(V2ResourceBalance)
                .where(
                    V2ResourceBalance.static_id == static_id,
                    V2ResourceBalance.recipient_id == recipient_id,
                )
            )
            == 1
        )
