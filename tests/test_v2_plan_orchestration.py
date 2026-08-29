"""Orchestration boundary tests for Regular and Split V2 planning."""

from datetime import date, datetime
from unittest.mock import patch

import pytest

from app.models import ClearMode, ReclearWeek, V2Plan
from app.services.reclear import create_reclear_week
from app.services.v2_plan_orchestration import (
    V2PlanOrchestrationError,
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
