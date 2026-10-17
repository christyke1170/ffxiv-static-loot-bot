"""Orchestration boundary tests for Regular and Split V2 planning."""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    CharacterKind,
    ClearMode,
    ReclearWeek,
    ReclearWorkflowState,
    V2Plan,
    V2ResourceBalance,
)
from app.services.neutral_resources import adjust_current_balance, set_current_balance
from app.services.reclear import create_reclear_week
from app.services.v2_plan_orchestration import (
    V2PlanOrchestrationError,
    close_v2_week,
    generate_and_persist_weekly_plan,
)
from tests.test_v2_planning_state import _static


def _setup(session, mode):
    static = _static(session)
    kwargs = {}
    if mode is ClearMode.SPLIT:
        kwargs["split_a_main_member_ids"] = {
            m.id for m in sorted(static.members, key=lambda m: m.id)[:4]
        }
    week = create_reclear_week(session, static, mode, week_start=date(2026, 8, 24), **kwargs)
    return static, week


def test_regular_mode_dispatches_regular_v2_only(session):
    static, week = _setup(session, ClearMode.REGULAR)
    result = generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    assert result.proposal.mode is ClearMode.REGULAR


def test_split_mode_dispatches_split_v2_only(session):
    static, week = _setup(session, ClearMode.SPLIT)
    result = generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    assert result.proposal.mode is ClearMode.SPLIT


def test_reclear_week_sets_creation_time_for_sqlite(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.SPLIT, week_start=date(2026, 8, 24))

    assert isinstance(week.created_at, datetime)
    assert session.get(ReclearWeek, week.id).created_at is not None


def test_split_planning_works_without_saved_groups(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.SPLIT, week_start=date(2026, 8, 24))
    week.groups[:] = []
    session.commit()
    result = generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    assert len(result.proposal.groups) == 2


def test_existing_identical_plan_returns_readback_without_regeneration(session):
    static, week = _setup(session, ClearMode.REGULAR)
    first = generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    with patch("app.services.v2_plan_orchestration.generate_regular_plan_v2") as planner:
        second = generate_and_persist_weekly_plan(session, static.id, week.id, 200)
    planner.assert_not_called()
    assert second == first


def test_one_plan_per_week_is_enforced(session):
    static, week = _setup(session, ClearMode.REGULAR)
    generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    assert session.query(V2Plan).filter_by(reclear_week_id=week.id).count() == 1


def test_missing_actor_is_rejected(session):
    static, week = _setup(session, ClearMode.REGULAR)
    with pytest.raises(V2PlanOrchestrationError, match="actor"):
        generate_and_persist_weekly_plan(session, static.id, week.id, None)


def test_planner_failure_rolls_back(session):
    static, week = _setup(session, ClearMode.REGULAR)
    with (
        patch(
            "app.services.v2_plan_orchestration.generate_regular_plan_v2",
            side_effect=ValueError("bad planner"),
        ),
        pytest.raises(ValueError),
    ):
        generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    assert session.query(V2Plan).filter_by(reclear_week_id=week.id).count() == 0


def test_returned_result_is_immutable_value_data(session):
    static, week = _setup(session, ClearMode.REGULAR)
    result = generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    assert result.proposal.__dataclass_params__.frozen
    assert not hasattr(result.proposal, "static")


def test_existing_plan_with_stale_current_state_is_rejected(session):
    static, week = _setup(session, ClearMode.REGULAR)
    generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    week.hierarchy_snapshot[0].job_abbreviation = "CHANGED"
    session.commit()
    with pytest.raises(V2PlanOrchestrationError, match="changed"):
        generate_and_persist_weekly_plan(session, static.id, week.id, 100)


def test_persistence_exception_leaves_no_v2_rows(session):
    static, week = _setup(session, ClearMode.REGULAR)
    with (
        patch(
            "app.services.v2_plan_orchestration.persist_regular_plan_v2",
            side_effect=ValueError("persist"),
        ),
        pytest.raises(ValueError),
    ):
        generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    assert session.query(V2Plan).filter_by(reclear_week_id=week.id).count() == 0


def test_correct_planner_is_called_exactly_once(session):
    static, week = _setup(session, ClearMode.REGULAR)
    with patch(
        "app.services.v2_plan_orchestration.generate_regular_plan_v2",
        wraps=__import__(
            "app.services.regular_planning_v2", fromlist=["generate_regular_plan_v2"]
        ).generate_regular_plan_v2,
    ) as planner:
        generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    planner.assert_called_once()


def test_orchestration_does_not_import_retired_planner_or_persistence_modules():
    import sys

    assert "app.services.loot_planning" not in sys.modules
    assert "app.services.loot_plan_persistence" not in sys.modules


@pytest.mark.parametrize("mode", (ClearMode.REGULAR, ClearMode.SPLIT))
def test_closure_awards_one_book_per_floor_to_saved_plan_participants(session, mode):
    static = _static(session)
    # Split planning may generate its own groups, without legacy participants.
    week = create_reclear_week(session, static, mode, week_start=date(2026, 8, 24))
    generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    main = next(c for m in static.members for c in m.characters if c.kind is CharacterKind.MAIN)
    set_current_balance(session, static, main, "BOOK_FLOOR_1", 5)
    set_current_balance(session, static, main, "ARMOR_TWINE", 2)

    close_v2_week(session, week, 100)
    session.commit()

    expected_ids = {
        c.id for m in static.members for c in m.characters if c.kind is CharacterKind.MAIN
    }
    books = {
        (row.recipient_id, row.resource_key): row.quantity
        for row in session.scalars(select(V2ResourceBalance))
        if row.resource_key.startswith("BOOK_")
    }
    expected = {
        (identifier, f"BOOK_FLOOR_{floor}"): 1
        for identifier in expected_ids
        for floor in range(1, 5)
    }
    expected[main.id, "BOOK_FLOOR_1"] = 6
    assert books == expected
    assert week.workflow_state is ReclearWorkflowState.CLOSED
    assert week.finalized_at is not None
    assert (
        session.scalar(
            select(V2ResourceBalance.quantity).where(
                V2ResourceBalance.resource_key == "ARMOR_TWINE"
            )
        )
        == 2
    )


def test_repeated_closure_from_stale_session_does_not_duplicate_books(session):
    static, week = _setup(session, ClearMode.REGULAR)
    generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    with Session(session.get_bind(), expire_on_commit=False) as stale_session:
        stale_week = stale_session.get(ReclearWeek, week.id)
        assert stale_week.workflow_state is not ReclearWorkflowState.CLOSED
        close_v2_week(session, week, 100)
        session.commit()
        close_v2_week(stale_session, stale_week, 200)
        stale_session.commit()
        close_v2_week(stale_session, stale_week, 200)
        stale_session.commit()

    assert len(list(session.scalars(select(V2ResourceBalance)))) == 32
    assert set(session.scalars(select(V2ResourceBalance.quantity))) == {1}
    assert (
        len(
            list(
                session.scalars(select(AuditLog).where(AuditLog.action == "V2_RECLEAR_WEEK_CLOSED"))
            )
        )
        == 1
    )


def test_book_award_failure_rolls_back_closure_and_partial_balances(session):
    static, week = _setup(session, ClearMode.REGULAR)
    generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    with (
        patch(
            "app.services.v2_plan_orchestration.adjust_current_balance",
        ) as award,
        pytest.raises(RuntimeError, match="award failed"),
    ):

        def fail_after_first(*args):
            if award.call_count == 1:
                return adjust_current_balance(*args)
            raise RuntimeError("award failed")

        award.side_effect = fail_after_first
        close_v2_week(session, week, 100)
    session.commit()
    session.refresh(week)
    assert week.workflow_state is not ReclearWorkflowState.CLOSED
    assert week.finalized_at is None
    assert not list(session.scalars(select(V2ResourceBalance)))
    close_v2_week(session, week, 100)
    assert len(list(session.scalars(select(V2ResourceBalance)))) == 32


def test_closure_and_book_awards_follow_the_callers_transaction(session):
    static, week = _setup(session, ClearMode.REGULAR)
    generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    close_v2_week(session, week, 100)
    session.rollback()
    session.refresh(week)
    assert week.workflow_state is not ReclearWorkflowState.CLOSED
    assert not list(session.scalars(select(V2ResourceBalance)))
    assert not list(
        session.scalars(select(AuditLog).where(AuditLog.action == "V2_RECLEAR_WEEK_CLOSED"))
    )


@pytest.mark.parametrize("cancelled", (False, True))
def test_unplanned_or_cancelled_week_does_not_award_books(session, cancelled):
    static, week = _setup(session, ClearMode.REGULAR)
    if cancelled:
        week.workflow_state = ReclearWorkflowState.CANCELLED
    session.commit()
    with pytest.raises(V2PlanOrchestrationError):
        close_v2_week(session, week, 100)
    session.refresh(week)
    assert week.workflow_state is (
        ReclearWorkflowState.CANCELLED if cancelled else ReclearWorkflowState.DRAFT
    )
    assert not list(session.scalars(select(V2ResourceBalance)))


def test_next_weeks_book_award_adds_to_manually_updated_balance(session):
    static, week = _setup(session, ClearMode.REGULAR)
    generate_and_persist_weekly_plan(session, static.id, week.id, 100)
    close_v2_week(session, week, 100)
    main = next(c for m in static.members for c in m.characters if c.kind is CharacterKind.MAIN)
    set_current_balance(session, static, main, "BOOK_FLOOR_1", 0)
    session.commit()
    next_week = create_reclear_week(
        session, static, ClearMode.REGULAR, week_start=date(2026, 8, 31)
    )
    generate_and_persist_weekly_plan(session, static.id, next_week.id, 100)
    close_v2_week(session, next_week, 100)
    balances = {
        row.resource_key: row.quantity
        for row in session.scalars(
            select(V2ResourceBalance).where(V2ResourceBalance.recipient_id == main.id)
        )
    }
    assert balances == {f"BOOK_FLOOR_{floor}": 1 if floor == 1 else 2 for floor in range(1, 5)}
