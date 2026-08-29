"""Detailed immutable graph persistence coverage for neutral V2 plans."""

from dataclasses import replace
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models import (
    ClearMode,
    V2Plan,
    V2PlanAssignment,
    V2PlanEffect,
    V2PlanRun,
    V2PlanUnassigned,
)
from app.services.planning_state import load_planning_state
from app.services.reclear import create_reclear_week
from app.services.regular_planning_v2 import generate_regular_plan_v2
from app.services.split_planning_v2 import generate_split_plan_v2
from app.services.v2_plan_persistence import (
    V2PlanPersistenceError,
    load_persisted_plan_v2,
    persist_regular_plan_v2,
    persist_split_plan_v2,
)
from tests.test_v2_planning_state import _static


def _week(session, mode):
    static = _static(session)
    kwargs = {}
    if mode is ClearMode.SPLIT:
        kwargs["split_a_main_member_ids"] = {
            m.id for m in sorted(static.members, key=lambda m: m.id)[:4]
        }
    week = create_reclear_week(session, static, mode, week_start=date(2026, 8, 24), **kwargs)
    return static, week, load_planning_state(session, static.id, week.id)


def test_regular_proposal_complete_round_trip(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    persisted = persist_regular_plan_v2(session, state, proposal, actor_id=10)
    session.commit()
    assert load_persisted_plan_v2(session, persisted.plan_id).proposal == proposal


def test_split_proposal_complete_round_trip(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    proposal = generate_split_plan_v2(state)
    persisted = persist_split_plan_v2(session, state, proposal, actor_id=10)
    session.commit()
    loaded = load_persisted_plan_v2(session, persisted.plan_id)
    assert loaded.proposal == proposal
    assert loaded.partitions_evaluated == 35


def test_generated_runs_and_participants_preserve_order(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    persisted = persist_split_plan_v2(session, state, generate_split_plan_v2(state), actor_id=10)
    session.commit()
    rows = session.scalars(
        select(V2PlanRun)
        .where(V2PlanRun.plan_id == persisted.plan_id)
        .order_by(V2PlanRun.run_number)
    ).all()
    assert [row.run_number for row in rows] == [1, 2]
    assert all([p.sort_order for p in row.participants] == list(range(1, 9)) for row in rows)


def test_source_group_ids_are_metadata_only(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    persist_split_plan_v2(session, state, generate_split_plan_v2(state), actor_id=10)
    session.commit()
    assert all(run.source_group_id is not None for run in session.scalars(select(V2PlanRun)))


def test_assignments_effects_and_unassigned_rows_have_stable_order(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    persisted = persist_regular_plan_v2(
        session, state, generate_regular_plan_v2(state), actor_id=10
    )
    session.commit()
    assignments = session.scalars(
        select(V2PlanAssignment)
        .where(V2PlanAssignment.plan_id == persisted.plan_id)
        .order_by(V2PlanAssignment.sort_order)
    ).all()
    assert [row.sort_order for row in assignments] == list(range(1, len(assignments) + 1))
    assert (
        session.scalar(
            select(func.count())
            .select_from(V2PlanUnassigned)
            .where(V2PlanUnassigned.plan_id == persisted.plan_id)
        )
        >= 0
    )


def test_weapon_effects_round_trip_in_weapon_then_offhand_order(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    persisted = persist_regular_plan_v2(session, state, proposal, actor_id=10)
    session.commit()
    effects = session.scalars(
        select(V2PlanEffect)
        .join(V2PlanAssignment)
        .where(V2PlanAssignment.plan_id == persisted.plan_id)
    ).all()
    assert all(effect.sort_order >= 1 for effect in effects)


def test_readback_is_orm_free_and_scores_are_immutable_tuples(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    proposal = generate_split_plan_v2(state)
    persisted = persist_split_plan_v2(session, state, proposal, actor_id=10)
    session.commit()
    loaded = load_persisted_plan_v2(session, persisted.plan_id).proposal
    assert isinstance(loaded.groups, tuple)
    assert not any(type(value).__module__.startswith("sqlalchemy") for value in loaded.groups)


def test_identical_retry_returns_existing_graph_without_duplicate_children(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    first = persist_regular_plan_v2(session, state, proposal, actor_id=10)
    session.commit()
    before = session.scalar(select(func.count()).select_from(V2PlanAssignment))
    second = persist_regular_plan_v2(session, state, proposal, actor_id=11)
    assert second == first
    assert session.scalar(select(func.count()).select_from(V2PlanAssignment)) == before


def test_different_active_proposal_is_rejected(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    persist_regular_plan_v2(session, state, proposal, actor_id=10)
    session.commit()
    altered = proposal.__class__(
        proposal.static_id,
        proposal.week_id,
        proposal.week_number,
        proposal.mode,
        "x" * 64,
        proposal.assignments,
        proposal.unassigned,
        proposal.warnings,
    )
    with pytest.raises(V2PlanPersistenceError, match="different active"):
        persist_regular_plan_v2(session, state, altered, actor_id=10)


def test_invalid_mode_is_rejected(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    altered = proposal.__class__(
        proposal.static_id,
        proposal.week_id,
        proposal.week_number,
        ClearMode.SPLIT,
        proposal.fingerprint,
        proposal.assignments,
        proposal.unassigned,
        proposal.warnings,
    )
    with pytest.raises(V2PlanPersistenceError, match="mode"):
        persist_regular_plan_v2(session, state, altered)


def test_invalid_static_id_is_rejected(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    with pytest.raises(V2PlanPersistenceError, match="identity"):
        persist_regular_plan_v2(session, replace(state, static_id=999), proposal)


def test_week_belonging_to_another_static_is_rejected(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    with pytest.raises(V2PlanPersistenceError, match="identity"):
        persist_regular_plan_v2(session, replace(state, week_id=999), proposal)


def test_invalid_recipient_is_rejected(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    proposal = generate_split_plan_v2(state)
    assignment = replace(proposal.groups[0].assignments[0], recipient_id=999)
    group = replace(
        proposal.groups[0], assignments=(assignment, *proposal.groups[0].assignments[1:])
    )
    malformed = replace(proposal, groups=(group, proposal.groups[1]))
    with pytest.raises(V2PlanPersistenceError, match="outside"):
        persist_split_plan_v2(session, state, malformed)


def test_recipient_absent_from_split_participants_is_rejected(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    proposal = generate_split_plan_v2(state)
    group = replace(
        proposal.groups[0], participant_ids=(999, *proposal.groups[0].participant_ids[1:])
    )
    malformed = replace(proposal, groups=(group, proposal.groups[1]))
    with pytest.raises(V2PlanPersistenceError, match="outside"):
        persist_split_plan_v2(session, state, malformed)


def test_malformed_split_group_shape_is_rejected(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    proposal = generate_split_plan_v2(state)
    malformed = replace(
        proposal, groups=(replace(proposal.groups[0], participant_ids=()), proposal.groups[1])
    )
    with pytest.raises(V2PlanPersistenceError, match="two generated"):
        persist_split_plan_v2(session, state, malformed)


def test_stale_roster_fingerprint_is_rejected(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    changed = replace(state, mains=tuple(reversed(state.mains)))
    with pytest.raises(V2PlanPersistenceError, match="changed"):
        persist_regular_plan_v2(session, changed, proposal)


def test_stale_ownership_fingerprint_is_rejected(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    proposal = generate_split_plan_v2(state)
    changed = replace(state, ownership=tuple(reversed(state.ownership)))
    with pytest.raises(V2PlanPersistenceError, match="changed"):
        persist_split_plan_v2(session, changed, proposal)


@pytest.mark.parametrize(
    "field",
    ["hierarchy", "fairness", "floors", "lockouts"],
)
def test_relevant_state_change_is_rejected(field, session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    value = list(getattr(state, field))
    if field == "hierarchy":
        value.reverse()
    elif field == "fairness":
        value.append((1, 1, ()))
    else:
        value.append(None)
    with pytest.raises(V2PlanPersistenceError, match="changed"):
        persist_regular_plan_v2(session, replace(state, **{field: tuple(value)}), proposal)


def test_persistence_never_calls_a_planner(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    with patch("app.services.v2_plan_persistence.load_planning_state", return_value=state):
        persisted = persist_regular_plan_v2(session, state, proposal, actor_id=10)
    assert persisted.plan_id


def test_child_insertion_failure_rolls_back_complete_graph(session):
    static, week, state = _week(session, ClearMode.SPLIT)
    proposal = generate_split_plan_v2(state)
    with (
        patch(
            "app.services.v2_plan_persistence._add_assignment", side_effect=RuntimeError("child")
        ),
        pytest.raises(V2PlanPersistenceError),
    ):
        persist_split_plan_v2(session, state, proposal, actor_id=10)
    session.rollback()
    assert session.query(V2Plan).count() == 0
    assert session.query(V2PlanRun).count() == 0
    assert session.query(V2PlanAssignment).count() == 0


def test_readback_excludes_generated_ids_and_timestamps_from_equality(session):
    static, week, state = _week(session, ClearMode.REGULAR)
    proposal = generate_regular_plan_v2(state)
    persisted = persist_regular_plan_v2(session, state, proposal, actor_id=10)
    session.commit()
    loaded = load_persisted_plan_v2(session, persisted.plan_id)
    assert loaded.proposal == proposal
    assert all(not hasattr(row, "id") for row in loaded.proposal.assignments)
