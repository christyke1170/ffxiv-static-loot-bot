"""Persistence boundary for the read-only Step 2-5 loot planners."""

import json
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.loot_planning_config import REGULAR_TRACKED_DROPS
from app.models import (
    AuditLog,
    Character,
    CharacterKind,
    ClearMode,
    FloorLootRule,
    GearSlot,
    LootAssignment,
    LootAssignmentState,
    LootPlan,
    LootPlanParticipant,
    LootPlanRun,
    LootType,
    PlannedLootDisposition,
    RaidFloor,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    WeeklyLootPlanStatus,
)
from app.schemas.loot_plan_persistence import (
    ActiveLootPlanError,
    LootPlanStalenessState,
    LootPlanValidationError,
    PersistedLootAssignment,
    PersistedLootParticipant,
    PersistedLootPlanNotFound,
    PersistedLootPlanResult,
    PersistedLootRun,
)
from app.services.loot_plan_source import SOURCE_SNAPSHOT_VERSION, build_source_snapshot
from app.services.loot_planning import calculate_regular_loot_plan, plan_split_savage_loot
from app.services.weeks import ResetPeriodPolicy


def generate_and_persist_loot_plan(
    session, static_id: int, mode: ClearMode, creator_discord_user_id: int
) -> PersistedLootPlanResult:
    """Generate and atomically persist a READY snapshot without applying any loot."""
    if not isinstance(mode, ClearMode):
        raise LootPlanValidationError("Unsupported loot-plan mode.")
    proposal = (
        calculate_regular_loot_plan(session, static_id)
        if mode is ClearMode.REGULAR
        else plan_split_savage_loot(session, static_id)
    )
    _validate_proposal(session, proposal, static_id, mode)
    static = session.get(Static, static_id)
    target_week = proposal.target_week
    if target_week is None or static is None:
        raise LootPlanValidationError(
            "Planner did not provide an active static, tier, and target week."
        )
    tier = static.active_raid_tier
    if tier is None:
        raise LootPlanValidationError("The selected static has no active raid tier.")
    try:
        warning_messages = tuple(issue.message for issue in proposal.warnings)
        with session.begin_nested():
            week = _get_or_create_target_week(session, static, tier.id, target_week, mode)
            participant_ids = _proposal_character_ids(proposal, mode)
            source_snapshot, source_state_hash = build_source_snapshot(
                session, static.id, mode, target_week, tier.id, participant_ids
            )
            active = session.scalar(
                select(LootPlan).where(
                    LootPlan.reclear_week_id == week.id,
                    LootPlan.status.in_((WeeklyLootPlanStatus.DRAFT, WeeklyLootPlanStatus.READY)),
                )
            )
            if active is not None:
                raise ActiveLootPlanError("An active loot plan already targets this week.")
            plan = LootPlan(
                reclear_week=week,
                name=f"{mode.value.title()} generated plan",
                mode=mode,
                status=WeeklyLootPlanStatus.DRAFT,
                created_by_discord_user_id=creator_discord_user_id,
                source_snapshot_version=SOURCE_SNAPSHOT_VERSION,
                source_snapshot=source_snapshot,
                source_state_hash=source_state_hash,
            )
            _populate_plan(session, plan, proposal, tier.id)
            session.add(plan)
            session.flush()
            plan.status = WeeklyLootPlanStatus.READY
            session.add(
                AuditLog(
                    static_id=static.id,
                    actor_discord_user_id=creator_discord_user_id,
                    action="LOOT_PLAN_CREATED",
                    entity_type="LootPlan",
                    entity_id=str(plan.id),
                    details=json.dumps(
                        {
                            "tier_id": tier.id,
                            "target_week": target_week,
                            "mode": mode.value,
                            "validation_warnings": warning_messages,
                        }
                    ),
                )
            )
            session.flush()
    except (IntegrityError, ValueError) as error:
        if isinstance(error, ActiveLootPlanError):
            raise
        raise LootPlanValidationError("The generated loot plan could not be persisted.") from error
    return _result_from_plan(plan, warning_messages)


def load_persisted_loot_plan(session, loot_plan_id: int) -> PersistedLootPlanResult:
    """Load a stored plan snapshot without recalculating or writing anything."""
    plan = session.scalar(
        select(LootPlan)
        .where(LootPlan.id == loot_plan_id)
        .options(
            joinedload(LootPlan.reclear_week).joinedload(ReclearWeek.static),
            joinedload(LootPlan.reclear_week).joinedload(ReclearWeek.raid_tier),
            selectinload(LootPlan.runs)
            .selectinload(LootPlanRun.participants)
            .joinedload(LootPlanParticipant.character)
            .joinedload(Character.job),
            selectinload(LootPlan.runs)
            .selectinload(LootPlanRun.assignments)
            .joinedload(LootAssignment.raid_floor),
            selectinload(LootPlan.runs)
            .selectinload(LootPlanRun.assignments)
            .joinedload(LootAssignment.loot_type),
            selectinload(LootPlan.runs)
            .selectinload(LootPlanRun.assignments)
            .joinedload(LootAssignment.intended_character)
            .joinedload(Character.job),
        )
    )
    if plan is None:
        raise PersistedLootPlanNotFound(f"Loot plan {loot_plan_id} was not found.")
    audit = session.scalar(
        select(AuditLog.details)
        .where(AuditLog.entity_type == "LootPlan", AuditLog.entity_id == str(plan.id))
        .order_by(AuditLog.id.desc())
    )
    warnings = ()
    if audit:
        warnings = tuple(json.loads(audit).get("validation_warnings", ()))
    return _result_from_plan(plan, warnings)


def _validate_proposal(session, proposal, static_id: int, mode: ClearMode) -> None:
    if not proposal.is_valid:
        raise LootPlanValidationError("The planner returned an invalid result.")
    if proposal.mode is not mode:
        raise LootPlanValidationError("Planner mode does not match requested mode.")
    if proposal.static is None or proposal.active_tier is None or proposal.target_week is None:
        raise LootPlanValidationError("Planner result is missing persistence identity.")
    if mode is ClearMode.REGULAR and proposal.run is None:
        raise LootPlanValidationError("Regular planner result is missing its run.")
    if mode is ClearMode.SPLIT and (
        proposal.winner is None or proposal.winner.run_a is None or proposal.winner.run_b is None
    ):
        raise LootPlanValidationError("Split planner result is missing its selected candidate.")
    static = session.get(Static, static_id)
    if static is None or proposal.static.name != static.name:
        raise LootPlanValidationError("Planner static does not match the requested static.")
    if static.active_raid_tier is None or proposal.active_tier.name != static.active_raid_tier.name:
        raise LootPlanValidationError("Planner tier does not match the active tier.")
    runs = _proposal_runs(proposal, mode)
    if not runs or any(not run.participants for run in runs):
        raise LootPlanValidationError("Planner result must contain runs with participants.")
    seen: set[tuple[str, int, int]] = set()
    for run in runs:
        participant_ids = {row.character_id for row in run.participants}
        if len(participant_ids) != len(run.participants):
            raise LootPlanValidationError("A run contains duplicate participants.")
        for row, _code, label, floor_number in _assignment_specs(proposal, mode, run):
            if row.disposition is PlannedLootDisposition.ASSIGNED:
                if row.recipient is None or row.recipient_designation is None:
                    raise LootPlanValidationError(
                        "Assigned loot must have a recipient and designation."
                    )
                if row.recipient.character_id not in participant_ids:
                    raise LootPlanValidationError("Assignment recipient is outside its run.")
                if row.recipient.designation is not row.recipient_designation:
                    raise LootPlanValidationError(
                        "Assignment recipient designation is inconsistent."
                    )
            elif row.recipient is not None:
                raise LootPlanValidationError("Free-roll loot cannot have a recipient.")
            key = (run.name, floor_number, label)
            if key in seen:
                raise LootPlanValidationError("A physical drop is duplicated in one run.")
            seen.add(key)
    if mode is ClearMode.SPLIT:
        for upgrade in proposal.winner.weapon_upgrades:
            if upgrade.recipient_designation is CharacterKind.MAIN:
                raise LootPlanValidationError("Weapon upgrades cannot be assigned to Mains.")
        for row in (*proposal.winner.twine_assignments, *proposal.winner.glaze_assignments):
            if row.recipient_designation is CharacterKind.ALT:
                raise LootPlanValidationError("Materials cannot be assigned to Alts.")
        for run in runs:
            pairs = [
                (row, label)
                for row, _code, label, _floor_number in _assignment_specs(proposal, mode, run)
                if label in {"Weapon Tomestone", "Weapon Augment"}
            ]
            if len(pairs) != 2 or {label for _row, label in pairs} != {
                "Weapon Tomestone",
                "Weapon Augment",
            }:
                raise LootPlanValidationError(
                    "Each Split run must contain one paired weapon proposal."
                )
            if pairs[0][0].recipient != pairs[1][0].recipient:
                raise LootPlanValidationError("Paired weapon components must share a recipient.")


def _proposal_runs(proposal, mode):
    if mode is ClearMode.REGULAR:
        return (proposal.run,)
    return (proposal.winner.run_a, proposal.winner.run_b)


def _proposal_character_ids(proposal, mode):
    return tuple(
        participant.character_id
        for run in _proposal_runs(proposal, mode)
        for participant in run.participants
    )


def _assignment_specs(proposal, mode, run):
    if mode is ClearMode.REGULAR:
        return tuple((row, None, row.loot_label, row.floor_number) for row in run.assignments)
    winner = proposal.winner
    rows = [(row, None, row.loot_label, row.floor_number) for row in run.assignments]
    rows.extend(
        (row, row.material_code, row.material_label, row.floor_number)
        for row in (*winner.twine_assignments, *winner.glaze_assignments)
        if row.run_name == run.name
    )
    for row in winner.weapon_upgrades:
        if row.run_name != run.name:
            continue
        rows.extend(
            (
                (row, "WEAPON_TOMESTONE", "Weapon Tomestone", row.tomestone_floor_number),
                (row, "WEAPON_AUGMENT", "Weapon Augment", row.augment_floor_number),
            )
        )
    return rows


def _get_or_create_target_week(session, static, tier_id, target_week, mode):
    closed_count = (
        session.scalar(
            select(func.count())
            .select_from(ReclearWeek)
            .where(
                ReclearWeek.static_id == static.id,
                ReclearWeek.workflow_state == ReclearWorkflowState.CLOSED,
            )
        )
        or 0
    )
    existing_weeks = list(
        session.scalars(
            select(ReclearWeek)
            .where(ReclearWeek.static_id == static.id)
            .order_by(ReclearWeek.week_start)
        )
    )
    expected_index = target_week - 1
    if expected_index < 0:
        raise LootPlanValidationError("Planner returned an invalid target week.")
    if expected_index < len(existing_weeks) and len(existing_weeks) > 1:
        week_start = existing_weeks[expected_index].week_start
    elif existing_weeks and any(
        row.workflow_state is not ReclearWorkflowState.CLOSED for row in existing_weeks
    ):
        week_start = next(
            row.week_start
            for row in reversed(existing_weeks)
            if row.workflow_state is not ReclearWorkflowState.CLOSED
        )
    elif existing_weeks:
        week_start = existing_weeks[-1].week_start.fromordinal(
            existing_weeks[-1].week_start.toordinal() + 7 * (target_week - len(existing_weeks))
        )
    else:
        if target_week != closed_count + 2:
            raise LootPlanValidationError("Planner target week is inconsistent with weekly state.")
        week_start = ResetPeriodPolicy().week_start(date.today())
    week = session.scalar(
        select(ReclearWeek).where(
            ReclearWeek.static_id == static.id,
            ReclearWeek.week_start == week_start,
        )
    )
    if week is None:
        week = ReclearWeek(
            static=static, raid_tier_id=tier_id, week_start=week_start, clear_mode=mode
        )
        session.add(week)
        session.flush()
    if week.raid_tier_id != tier_id or week.clear_mode is not mode:
        raise LootPlanValidationError(
            "Target week does not belong to the active static tier and mode."
        )
    return week


def _populate_plan(session, plan, proposal, tier_id):
    mode = plan.mode
    runs = _proposal_runs(proposal, mode)
    rules = list(
        session.scalars(
            select(FloorLootRule)
            .join(FloorLootRule.raid_floor)
            .join(FloorLootRule.loot_type)
            .where(RaidFloor.raid_tier_id == tier_id)
            .options(joinedload(FloorLootRule.raid_floor), joinedload(FloorLootRule.loot_type))
        )
    )
    by_code_floor = {(r.raid_floor.floor_number, r.loot_type.code): r for r in rules}
    loot_types = {
        loot_type.code: loot_type
        for loot_type in session.scalars(select(LootType).where(LootType.raid_tier_id == tier_id))
    }
    floors = {
        floor.floor_number: floor
        for floor in session.scalars(select(RaidFloor).where(RaidFloor.raid_tier_id == tier_id))
    }
    persisted_runs = {}
    for run_number, source_run in enumerate(runs, 1):
        target = LootPlanRun(loot_plan=plan, run_number=run_number, name=source_run.name)
        target.participants = [
            LootPlanParticipant(character_id=row.character_id, designation=row.designation)
            for row in source_run.participants
        ]
        persisted_runs[source_run.name] = target
    order = 0
    assignments = []
    for source_run in runs:
        target_run = persisted_runs[source_run.name]
        for row, code, label, floor_number in _assignment_specs(proposal, mode, source_run):
            floor, loot_type = _resolve_configuration(
                by_code_floor, floors, loot_types, code, label, floor_number, mode
            )
            order += 1
            assignment = LootAssignment(
                loot_plan=plan,
                plan_run=target_run,
                raid_floor=floor,
                loot_type=loot_type,
                intended_character_id=(row.recipient.character_id if row.recipient else None),
                intended_bis_set_item_id=getattr(row, "intended_bis_set_item_id", None),
                gear_slot_id=(
                    session.scalar(select(GearSlot.id).where(GearSlot.code == row.gear_slot))
                    if getattr(row, "gear_slot", None) is not None
                    else None
                ),
                resulting_classification=getattr(row, "resulting_classification", None),
                recipient_designation=row.recipient_designation,
                expected_drop_instance=1,
                disposition=row.disposition,
                state=(
                    LootAssignmentState.FREE_ROLL
                    if row.disposition is PlannedLootDisposition.FREE_ROLL
                    else LootAssignmentState.PROPOSED
                ),
                planning_reason=row.explanation,
                sort_order=order,
            )
            assignments.append((label, row, assignment))
    pair_rows = defaultdict(list)
    if mode is ClearMode.SPLIT:
        for label, row, assignment in assignments:
            if label in {"Weapon Tomestone", "Weapon Augment"}:
                pair_rows[
                    (
                        assignment.plan_run.name,
                        row.recipient.character_id if row.recipient else None,
                    )
                ].append(assignment)
        session.flush()
        for pair in (values for values in pair_rows.values() if len(values) == 2):
            first, second = pair
            first.paired_assignment = second


def _resolve_configuration(by_code_floor, floors, loot_types, code, label, floor_number, mode):
    code = code or _label_code(label)
    rule = by_code_floor.get((floor_number, code)) if code else None
    loot_type = loot_types.get(code) if code else None
    floor = floors.get(floor_number)
    if rule is not None:
        loot_type = rule.loot_type
        floor = rule.raid_floor
    if floor is None or loot_type is None or loot_type.raid_tier_id != floor.raid_tier_id:
        raise LootPlanValidationError(f"Missing authoritative loot configuration for {label}.")
    if mode is ClearMode.REGULAR and loot_type.code not in {
        drop.loot_type_code for drop in REGULAR_TRACKED_DROPS
    }:
        raise LootPlanValidationError("Regular plan contains unsupported loot.")
    return floor, loot_type


def _label_code(label):
    mapping = {
        "Earring Coffer": "EARRING_COFFER",
        "Necklace Coffer": "NECKLACE_COFFER",
        "Bracelet Coffer": "BRACELET_COFFER",
        "Ring Coffer": "RING_COFFER",
        "Head Coffer": "HEAD_COFFER",
        "Gloves Coffer": "GLOVES_COFFER",
        "Boots Coffer": "BOOTS_COFFER",
        "Chest Coffer": "CHEST_COFFER",
        "Pants Coffer": "PANTS_COFFER",
        "Weapon Coffer": "WEAPON_COFFER",
        "Glaze": "ACCESSORY_GLAZE",
        "Twine": "ARMOR_TWINE",
        "Weapon Tomestone": "WEAPON_TOMESTONE",
        "Weapon Augment": "WEAPON_AUGMENT",
    }
    return mapping.get(label)


def _result_from_plan(plan, warnings=()):
    week = plan.reclear_week
    runs = []
    for run in sorted(plan.runs, key=lambda r: r.run_number):
        participants = tuple(
            PersistedLootParticipant(
                p.character_id,
                p.character.name,
                p.character.world,
                p.character.job.abbreviation,
                p.designation,
                i,
            )
            for i, p in enumerate(run.participants, 1)
        )
        rows = tuple(
            PersistedLootAssignment(
                a.id,
                a.raid_floor.floor_number,
                a.raid_floor.name,
                _persisted_loot_label(a.loot_type.code),
                a.disposition,
                a.intended_character_id,
                a.intended_character.name if a.intended_character else None,
                a.intended_character.job.abbreviation if a.intended_character else None,
                a.recipient_designation,
                a.expected_drop_instance,
                a.paired_assignment_id,
            )
            for a in sorted(
                run.assignments, key=lambda a: (a.raid_floor.floor_number, a.sort_order, a.id)
            )
        )
        runs.append(PersistedLootRun(run.id, run.run_number, run.name, participants, rows))
    return PersistedLootPlanResult(
        plan.id,
        week.static.id,
        week.static.name,
        week.raid_tier.id,
        week.raid_tier.name,
        _target_week_number(week),
        plan.mode,
        plan.status,
        plan.created_by_discord_user_id,
        plan.created_at,
        tuple(runs),
        validation_warnings=tuple(warnings),
        snapshot_version=plan.source_snapshot_version,
        stored_source_hash=plan.source_state_hash,
        staleness=(
            LootPlanStalenessState.CURRENT
            if plan.source_snapshot_version == SOURCE_SNAPSHOT_VERSION
            and plan.source_snapshot
            and plan.source_state_hash
            else LootPlanStalenessState.UNVERIFIABLE
        ),
        confirmation_blocked=not (
            plan.source_snapshot_version == SOURCE_SNAPSHOT_VERSION
            and plan.source_snapshot
            and plan.source_state_hash
        ),
        applied_at=plan.applied_at,
        cancelled_at=plan.cancelled_at,
    )


def _target_week_number(week):
    return (
        sum(row.workflow_state is ReclearWorkflowState.CLOSED for row in week.static.reclear_weeks)
        + 2
    )


def _persisted_loot_label(code: str) -> str:
    labels = {
        "EARRING_COFFER": "Earring Coffer",
        "NECKLACE_COFFER": "Necklace Coffer",
        "BRACELET_COFFER": "Bracelet Coffer",
        "RING_COFFER": "Ring Coffer",
        "HEAD_COFFER": "Head Coffer",
        "GLOVES_COFFER": "Gloves Coffer",
        "BOOTS_COFFER": "Boots Coffer",
        "CHEST_COFFER": "Chest Coffer",
        "PANTS_COFFER": "Pants Coffer",
        "WEAPON_COFFER": "Weapon Coffer",
        "ACCESSORY_GLAZE": "Glaze",
        "ARMOR_TWINE": "Twine",
        "WEAPON_TOMESTONE": "Weapon Tomestone",
        "WEAPON_AUGMENT": "Weapon Augment",
    }
    return labels.get(code, code)
