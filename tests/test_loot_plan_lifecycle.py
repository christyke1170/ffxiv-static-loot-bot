"""Focused Step 7 source-snapshot and lifecycle tests."""

import json

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    CharacterGearSlot,
    ClearMode,
    GearClassification,
    GearSlot,
    LootPlan,
    WeeklyLootPlanStatus,
)
from app.schemas import (
    LootPlanStalenessState,
    LootPlanStaleReasonCode,
    PersistedLootPlanNotFound,
)
from app.services import (
    cancel_loot_plan,
    check_loot_plan_staleness,
    generate_and_persist_loot_plan,
    load_active_loot_plan,
)
from app.services.loot_plan_source import build_source_snapshot
from tests.test_regular_loot_planning import RegularFixture


def test_ready_plan_stores_deterministic_source_snapshot(session):
    fixture = RegularFixture(session)
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 8010)
    plan = session.get(LootPlan, result.plan_id)

    assert plan.source_snapshot_version == 1
    assert plan.source_snapshot
    assert len(plan.source_state_hash) == 64
    assert json.loads(plan.source_snapshot)["version"] == 1
    snapshot, digest = build_source_snapshot(
        session,
        fixture.static.id,
        ClearMode.REGULAR,
        result.target_week,
        fixture.tier.id,
        tuple(row.character_id for row in plan.runs[0].participants),
    )
    assert snapshot == plan.source_snapshot
    assert digest == plan.source_state_hash


def test_staleness_is_current_then_detects_gear_change(session):
    fixture = RegularFixture(session)
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 8011)
    assert (
        check_loot_plan_staleness(session, result.plan_id).state is LootPlanStalenessState.CURRENT
    )

    slot = CharacterGearSlot(
        character_id=fixture.mains[0].id,
        gear_slot_id=session.scalar(select(GearSlot.id).order_by(GearSlot.sort_order)),
        current_classification=GearClassification.TOME,
    )
    session.add(slot)
    session.flush()
    stale = check_loot_plan_staleness(session, result.plan_id)
    assert stale.state is LootPlanStalenessState.STALE
    assert any(reason.code is LootPlanStaleReasonCode.CHARACTER_CHANGED for reason in stale.reasons)


def test_books_and_audits_do_not_change_source_hash(session):
    fixture = RegularFixture(session)
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 8012)
    plan = session.get(LootPlan, result.plan_id)
    before = plan.source_state_hash
    session.add(AuditLog(static=fixture.static, action="UNRELATED", entity_type="Test"))
    assert (
        check_loot_plan_staleness(session, result.plan_id).state is LootPlanStalenessState.CURRENT
    )
    assert plan.source_state_hash == before


def test_active_lookup_and_cancellation_preserve_history(session):
    fixture = RegularFixture(session)
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 8013)
    active = load_active_loot_plan(session, fixture.static.id)
    assert active.plan_id == result.plan_id
    runs = len(active.runs)
    cancelled = cancel_loot_plan(session, result.plan_id, 99001)
    assert cancelled.status is WeeklyLootPlanStatus.CANCELLED
    assert cancelled.cancelled_at is not None
    assert len(cancelled.runs) == runs
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "LOOT_PLAN_CANCELLED")
        )
        == 1
    )
    with pytest.raises(PersistedLootPlanNotFound):
        load_active_loot_plan(session, fixture.static.id)


def test_missing_legacy_snapshot_is_unverifiable(session):
    fixture = RegularFixture(session)
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 8014)
    plan = session.get(LootPlan, result.plan_id)
    plan.source_snapshot_version = None
    plan.source_snapshot = None
    plan.source_state_hash = None
    assert (
        check_loot_plan_staleness(session, result.plan_id).state
        is LootPlanStalenessState.UNVERIFIABLE
    )


def test_active_lookup_unknown_scope_is_typed(session):
    fixture = RegularFixture(session)
    with pytest.raises(PersistedLootPlanNotFound):
        load_active_loot_plan(session, fixture.static.id)
