"""Read-only lifecycle inspection and atomic cancellation for loot plans."""

import json
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import AuditLog, LootPlan, ReclearWeek, Static, WeeklyLootPlanStatus
from app.schemas.loot_plan_persistence import (
    ActiveLootPlanConflict,
    LootPlanStalenessResult,
    LootPlanStalenessState,
    LootPlanStaleReason,
    LootPlanStaleReasonCode,
    LootPlanValidationError,
    PersistedLootPlanNotFound,
    PersistedLootPlanResult,
)
from app.services.loot_plan_persistence import load_persisted_loot_plan
from app.services.loot_plan_source import SOURCE_SNAPSHOT_VERSION, build_source_snapshot
from app.services.transactions import entity_lock


def check_loot_plan_staleness(session, loot_plan_id: int) -> LootPlanStalenessResult:
    plan = session.get(LootPlan, loot_plan_id)
    if plan is None:
        raise PersistedLootPlanNotFound(f"Loot plan {loot_plan_id} was not found.")
    if not plan.source_snapshot:
        return LootPlanStalenessResult(
            LootPlanStalenessState.UNVERIFIABLE,
            (
                LootPlanStaleReason(
                    LootPlanStaleReasonCode.SNAPSHOT_MISSING,
                    "This plan has no authoritative source snapshot.",
                ),
            ),
        )
    if plan.source_snapshot_version != SOURCE_SNAPSHOT_VERSION:
        return LootPlanStalenessResult(
            LootPlanStalenessState.UNVERIFIABLE,
            (
                LootPlanStaleReason(
                    LootPlanStaleReasonCode.SNAPSHOT_VERSION_UNSUPPORTED,
                    "This plan uses an unsupported source snapshot version.",
                ),
            ),
        )
    week = session.get(ReclearWeek, plan.reclear_week_id)
    participant_ids = tuple(
        participant.character_id for run in plan.runs for participant in run.participants
    )
    static = session.get(Static, week.static_id)
    current, current_hash = build_source_snapshot(
        session,
        week.static_id,
        plan.mode,
        _target_week_number(session, week.static_id),
        static.active_raid_tier_id,
        participant_ids,
    )
    if current_hash == plan.source_state_hash:
        return LootPlanStalenessResult(LootPlanStalenessState.CURRENT)
    old = json.loads(plan.source_snapshot)
    new = json.loads(current)
    reasons = _reasons(old, new)
    if not reasons:
        reasons = (
            LootPlanStaleReason(
                LootPlanStaleReasonCode.SOURCE_STATE_CHANGED,
                "Authoritative planning state changed.",
            ),
        )
    return LootPlanStalenessResult(LootPlanStalenessState.STALE, tuple(reasons))


def load_active_loot_plan(
    session, static_id: int, raid_tier_id: int | None = None, target_week: int | None = None
) -> PersistedLootPlanResult:
    static = session.get(Static, static_id)
    if static is None:
        raise PersistedLootPlanNotFound(f"Static {static_id} was not found.")
    if target_week is None:
        target_week = _target_week_number(session, static_id)
    tier_id = raid_tier_id or static.active_raid_tier_id
    candidates = [
        plan
        for plan in session.scalars(
            select(LootPlan)
            .join(ReclearWeek)
            .where(
                ReclearWeek.static_id == static_id,
                ReclearWeek.raid_tier_id == tier_id,
                LootPlan.status.in_((WeeklyLootPlanStatus.DRAFT, WeeklyLootPlanStatus.READY)),
            )
        )
        if _plan_target_week(plan) == target_week
    ]
    if not candidates:
        raise PersistedLootPlanNotFound("No active loot plan targets the requested scope.")
    if len(candidates) > 1:
        raise ActiveLootPlanConflict("Multiple active loot plans target the requested scope.")
    return _with_staleness(session, candidates[0])


def cancel_loot_plan(session, loot_plan_id: int, actor_discord_user_id: int):
    with entity_lock("loot_plan", loot_plan_id), session.begin_nested():
        plan = session.get(LootPlan, loot_plan_id)
        if plan is None:
            raise PersistedLootPlanNotFound(f"Loot plan {loot_plan_id} was not found.")
        if plan.status is WeeklyLootPlanStatus.CANCELLED:
            return _with_staleness(session, plan)
        if plan.status not in (WeeklyLootPlanStatus.DRAFT, WeeklyLootPlanStatus.READY):
            raise LootPlanValidationError("Only DRAFT or READY plans can be cancelled.")
        prior = plan.status.value
        plan.status = WeeklyLootPlanStatus.CANCELLED
        plan.cancelled_at = datetime.now(UTC)
        session.add(
            AuditLog(
                static_id=plan.reclear_week.static_id,
                actor_discord_user_id=actor_discord_user_id,
                action="LOOT_PLAN_CANCELLED",
                entity_type="LootPlan",
                entity_id=str(plan.id),
                details=json.dumps({"prior_status": prior, "new_status": "CANCELLED"}),
            )
        )
        session.flush()
    return _with_staleness(session, plan)


def _with_staleness(session, plan):
    result = load_persisted_loot_plan(session, plan.id)
    stale = check_loot_plan_staleness(session, plan.id)
    return replace(
        result,
        staleness=stale.state,
        stale_reasons=stale.reasons,
        confirmation_blocked=stale.confirmation_blocked,
    )


def _target_week_number(session, static_id):
    weeks = list(session.scalars(select(ReclearWeek).where(ReclearWeek.static_id == static_id)))
    return sum(week.workflow_state.value == "CLOSED" for week in weeks) + 2


def _plan_target_week(plan):
    if not plan.source_snapshot:
        return None
    return json.loads(plan.source_snapshot).get("scope", {}).get("target_week")


def _reasons(old, new):
    reasons = []
    if old.get("scope", {}).get("completed_week") != new.get("scope", {}).get("completed_week"):
        reasons.append(
            LootPlanStaleReason(
                LootPlanStaleReasonCode.COMPLETED_WEEK_CHANGED,
                "The static's completed week changed.",
            )
        )
    if old.get("scope", {}).get("target_week") != new.get("scope", {}).get("target_week"):
        reasons.append(
            LootPlanStaleReason(
                LootPlanStaleReasonCode.TARGET_WEEK_CHANGED,
                "The plan target week no longer matches the next week.",
            )
        )
    if old.get("scope", {}).get("tier_id") != new.get("scope", {}).get("tier_id"):
        reasons.append(
            LootPlanStaleReason(
                LootPlanStaleReasonCode.ACTIVE_TIER_CHANGED,
                "The active raid tier changed.",
            )
        )
    if old.get("roster") != new.get("roster"):
        reasons.append(
            LootPlanStaleReason(
                LootPlanStaleReasonCode.ROSTER_CHANGED,
                "The static roster or character binding changed.",
            )
        )
    if old.get("characters") != new.get("characters"):
        reasons.append(
            LootPlanStaleReason(
                LootPlanStaleReasonCode.CHARACTER_CHANGED,
                "A participating character, BiS selection, gear, or resource changed.",
            )
        )
    if old.get("material_grants") != new.get("material_grants"):
        reasons.append(
            LootPlanStaleReason(
                LootPlanStaleReasonCode.MATERIAL_GRANT_CHANGED,
                "Confirmed reclear material grant history changed.",
            )
        )
    if old.get("configuration", {}).get("floors") != new.get("configuration", {}).get("floors"):
        reasons.append(
            LootPlanStaleReason(
                LootPlanStaleReasonCode.FLOOR_CONFIGURATION_CHANGED,
                "Raid floor loot configuration changed.",
            )
        )
    if old.get("configuration", {}).get("loot_types") != new.get("configuration", {}).get(
        "loot_types"
    ):
        reasons.append(
            LootPlanStaleReason(
                LootPlanStaleReasonCode.LOOT_CONFIGURATION_CHANGED,
                "Raid loot-type configuration changed.",
            )
        )
    return reasons
