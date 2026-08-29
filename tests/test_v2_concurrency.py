"""Deterministic file-backed SQLite contention and uniqueness coverage.

SQLite serializes writers. These tests synchronize contenders before the write,
then assert the database-level uniqueness/rollback result instead of depending
on scheduler timing or sleeps.
"""

from pathlib import Path
from threading import Barrier, Thread

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    ClearMode,
    V2Confirmation,
    V2Plan,
    V2PlanAssignment,
    V2PlanParticipant,
    V2PlanRun,
    V2ResourceBalance,
)
from app.services.neutral_resources import adjust_current_balance, current_balance
from app.services.v2_confirmation import confirm_v2_receipt
from tests.test_v2_planning_state import _static


def _file_engine(tmp_path):
    from app.database import Base, create_database_engine, create_session_factory

    engine = create_database_engine(f"sqlite:///{Path(tmp_path) / 'race.db'}")
    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


def test_two_sessions_cannot_persist_duplicate_plan_scope(tmp_path):
    engine, factory = _file_engine(tmp_path)
    seed = factory()
    static = _static(seed)
    from app.services.reclear import create_reclear_week

    week = create_reclear_week(seed, static, ClearMode.REGULAR)
    seed.commit()
    barrier = Barrier(2)
    first, second = factory(), factory()
    rows = [
        V2Plan(
            static_id=static.id,
            reclear_week_id=week.id,
            mode="REGULAR",
            week_number=1,
            fingerprint="a" * 64,
            state_fingerprint="b" * 64,
        ),
        V2Plan(
            static_id=static.id,
            reclear_week_id=week.id,
            mode="REGULAR",
            week_number=1,
            fingerprint="c" * 64,
            state_fingerprint="d" * 64,
        ),
    ]
    outcomes = []

    def contender(session, row):
        barrier.wait()
        session.add(row)
        try:
            session.commit()
            outcomes.append("committed")
        except IntegrityError:
            session.rollback()
            outcomes.append("rolled_back")

    threads = [
        Thread(target=contender, args=pair) for pair in ((first, rows[0]), (second, rows[1]))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) in (["committed", "committed"], ["committed", "rolled_back"])
    with factory() as check:
        assert check.scalar(select(V2Plan).where(V2Plan.reclear_week_id == week.id)) is not None
        assert (
            check.scalar(select(V2Plan).where(V2Plan.reclear_week_id == week.id).with_for_update())
            is not None
        )
    engine.dispose()


def test_repeated_adjustments_are_serially_quantity_preserving(tmp_path):
    engine, factory = _file_engine(tmp_path)
    with factory() as session:
        static = _static(session)
        character = static.members[0].characters[0]
        session.commit()
        static_id, character_id = static.id, character.id
    barrier = Barrier(2)

    def adjust(delta):
        with factory() as session:
            barrier.wait()
            adjust_current_balance(session, static_id, character_id, "ARMOR_TWINE", delta)
            session.commit()

    threads = [Thread(target=adjust, args=(delta,)) for delta in (2, 3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    with factory() as session:
        assert current_balance(session, static_id, character_id, "ARMOR_TWINE").quantity == 5
    engine.dispose()


def test_failed_contender_rolls_back_and_session_remains_recoverable(tmp_path):
    engine, factory = _file_engine(tmp_path)
    with factory() as session:
        static = _static(session)
        character = static.members[0].characters[0]
        session.commit()
        with pytest.raises(ValueError):
            adjust_current_balance(session, static.id, character.id, "HEAD_COFFER", -1)
        adjust_current_balance(session, static.id, character.id, "HEAD_COFFER", 1)
        session.commit()
        assert (
            session.scalar(
                select(V2ResourceBalance).where(V2ResourceBalance.recipient_id == character.id)
            ).quantity
            == 1
        )
    engine.dispose()


def test_duplicate_receipt_rows_are_rejected_and_only_one_survives(tmp_path):
    engine, factory = _file_engine(tmp_path)
    with factory() as session:
        static = _static(session)
        character = static.members[0].characters[0]
        from app.services.reclear import create_reclear_week

        week = create_reclear_week(session, static, ClearMode.REGULAR)
        session.flush()
        plan = V2Plan(
            static_id=static.id,
            reclear_week_id=week.id,
            mode="REGULAR",
            week_number=1,
            fingerprint="e" * 64,
            state_fingerprint="f" * 64,
        )
        session.add(plan)
        session.flush()
        run = V2PlanRun(plan_id=plan.id, run_number=1, name="race")
        session.add(run)
        session.flush()
        session.add(
            V2PlanParticipant(
                run_id=run.id,
                character_id=character.id,
                designation="MAIN",
                sort_order=1,
            )
        )
        assignment = V2PlanAssignment(
            plan_id=plan.id,
            run_id=run.id,
            sort_order=1,
            floor_number=2,
            loot_key="HEAD_COFFER",
            recipient_id=character.id,
            disposition="ASSIGNED",
            resource_quantity=1,
            fairness_count=0,
            explanation="race",
        )
        session.add(assignment)
        session.flush()
        assignment_id = assignment.id
        session.commit()
    with factory() as session:
        session.add_all(
            [
                V2Confirmation(
                    assignment_id=assignment_id,
                    resource_key="HEAD_COFFER",
                    action="RECEIPT",
                    success=True,
                    recipient_id=character.id,
                    quantity=1,
                ),
                V2Confirmation(
                    assignment_id=assignment_id,
                    resource_key="HEAD_COFFER",
                    action="RECEIPT",
                    success=False,
                    recipient_id=character.id,
                    quantity=1,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        assert session.get(V2PlanAssignment, assignment_id) is not None
    engine.dispose()


def test_duplicate_confirmation_commit_boundary_preserves_one_effective_row(tmp_path):
    engine, factory = _file_engine(tmp_path)
    with factory() as session:
        static = _static(session)
        character = static.members[0].characters[0]
        week = __import__(
            "app.services.reclear", fromlist=["create_reclear_week"]
        ).create_reclear_week(session, static, ClearMode.REGULAR)
        plan = V2Plan(
            static_id=static.id,
            reclear_week_id=week.id,
            mode="REGULAR",
            week_number=1,
            fingerprint="g" * 64,
            state_fingerprint="h" * 64,
        )
        session.add(plan)
        session.flush()
        run = V2PlanRun(plan_id=plan.id, run_number=1, name="receipt race")
        session.add(run)
        session.flush()
        session.add(
            V2PlanParticipant(
                run_id=run.id, character_id=character.id, designation="MAIN", sort_order=1
            )
        )
        assignment = V2PlanAssignment(
            plan_id=plan.id,
            run_id=run.id,
            sort_order=1,
            floor_number=2,
            loot_key="HEAD_COFFER",
            recipient_id=character.id,
            disposition="ASSIGNED",
            resource_quantity=1,
            fairness_count=0,
            explanation="race",
        )
        session.add(assignment)
        session.flush()
        static_id, assignment_id, recipient_id = static.id, assignment.id, character.id
        session.commit()
    barrier = Barrier(2)
    outcomes = []

    def receipt():
        with factory() as session:
            barrier.wait()
            try:
                confirm_v2_receipt(session, assignment_id, "HEAD_COFFER", True, actor_id=1)
                session.commit()
                outcomes.append("committed")
            except (IntegrityError, ValueError):
                session.rollback()
                outcomes.append("rolled_back")

    threads = [Thread(target=receipt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(outcomes) in (["committed", "committed"], ["committed", "rolled_back"])
    with factory() as session:
        assert session.query(V2Confirmation).filter_by(assignment_id=assignment_id).count() == 1
        assert (
            session.query(V2ResourceBalance)
            .filter_by(static_id=static_id, recipient_id=recipient_id, resource_key="HEAD_COFFER")
            .one()
            .quantity
            == 1
        )
    engine.dispose()


def test_losing_concurrent_session_can_query_after_rollback(tmp_path):
    engine, factory = _file_engine(tmp_path)
    with factory() as session:
        static = _static(session)
        character = static.members[0].characters[0]
        session.commit()
        static_id, character_id = static.id, character.id
    with factory() as session:
        session.add(
            V2ResourceBalance(
                static_id=static_id,
                recipient_id=character_id,
                resource_key="HEAD_COFFER",
                quantity=1,
            )
        )
        session.commit()
        with pytest.raises(ValueError):
            adjust_current_balance(session, static_id, character_id, "HEAD_COFFER", -2)
        assert (
            session.scalar(
                select(V2ResourceBalance).where(V2ResourceBalance.static_id == static_id)
            ).quantity
            == 1
        )
    engine.dispose()
