"""Transactional persistence for immutable Regular and Split V2 proposals."""

import json
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import (
    CharacterKind,
    ClearMode,
    GearClassification,
    GearSlotCode,
    V2Plan,
    V2PlanAssignment,
    V2PlanEffect,
    V2PlanParticipant,
    V2PlanRun,
    V2PlanUnassigned,
)
from app.schemas.regular_planning_v2 import (
    ProposedGearEffect,
    RegularAssignment,
    RegularPlanProposal,
    RegularScore,
    UnassignedRegularLoot,
)
from app.schemas.split_planning_v2 import (
    SplitAssignment,
    SplitGroupProposal,
    SplitPartitionScore,
    SplitPlanProposal,
    SplitScore,
    UnassignedSplitLoot,
)
from app.schemas.v2_plan_persistence import PersistedV2Plan
from app.services.planning_state import load_planning_state
from app.services.v2_plan_state_fingerprint import planning_state_fingerprint


class V2PlanPersistenceError(ValueError):
    """A V2 proposal cannot be persisted safely."""


def persist_regular_plan_v2(
    session, state, proposal, actor_id: int | None = None
) -> PersistedV2Plan:
    return _persist(session, state, proposal, ClearMode.REGULAR, actor_id)


def persist_split_plan_v2(session, state, proposal, actor_id: int | None = None) -> PersistedV2Plan:
    return _persist(session, state, proposal, ClearMode.SPLIT, actor_id)


def load_persisted_plan_v2(session, plan_id: int) -> PersistedV2Plan:
    plan = session.scalar(
        select(V2Plan)
        .where(V2Plan.id == plan_id)
        .options(
            selectinload(V2Plan.runs).selectinload(V2PlanRun.participants),
            selectinload(V2Plan.runs).selectinload(V2PlanRun.assignments),
            selectinload(V2Plan.assignments).selectinload(V2PlanAssignment.effects),
            selectinload(V2Plan.unassigned),
        )
    )
    if plan is None:
        raise V2PlanPersistenceError(f"V2 plan {plan_id} was not found.")
    runs = sorted(plan.runs, key=lambda row: row.run_number)
    if plan.mode == ClearMode.REGULAR.value:
        rows = tuple(
            _regular_assignment(row)
            for row in sorted(plan.assignments, key=lambda value: value.sort_order)
        )
        unassigned = tuple(
            UnassignedRegularLoot(
                row.floor_number,
                row.loot_key,
                _slot(row.primary_slot if hasattr(row, "primary_slot") else None),
                row.material_key,
                row.reason,
            )
            for row in sorted(plan.unassigned, key=lambda value: value.sort_order)
        )
        proposal = RegularPlanProposal(
            plan.static_id,
            plan.reclear_week_id,
            plan.week_number,
            ClearMode.REGULAR,
            plan.fingerprint,
            rows,
            unassigned,
            tuple(json.loads(plan.warnings_json)),
        )
    else:
        groups = []
        for run in runs:
            rows = tuple(
                _split_assignment(row, run)
                for row in sorted(run.assignments, key=lambda value: value.sort_order)
            )
            groups.append(
                SplitGroupProposal(
                    run.source_group_id if run.source_group_id is not None else run.id,
                    run.run_number,
                    tuple(
                        participant.character_id
                        for participant in sorted(
                            run.participants, key=lambda value: value.sort_order
                        )
                    ),
                    rows,
                )
            )
        unassigned = tuple(
            UnassignedSplitLoot(
                row.group_id,
                row.run_number,
                row.floor_number,
                row.loot_key,
                _slot(row.primary_slot),
                row.material_key,
                row.reason,
            )
            for row in sorted(plan.unassigned, key=lambda value: value.sort_order)
        )
        score = _score_from_json(plan.score_json)
        proposal = SplitPlanProposal(
            plan.static_id,
            plan.reclear_week_id,
            plan.week_number,
            ClearMode.SPLIT,
            plan.fingerprint,
            tuple(groups),
            unassigned,
            tuple(json.loads(plan.warnings_json)),
            score,
            plan.partitions_evaluated,
        )
    return PersistedV2Plan(plan.id, proposal)


def _persist(session, state, proposal, mode, actor_id=None):
    if proposal.static_id != state.static_id or proposal.week_id != state.week_id:
        raise V2PlanPersistenceError("Proposal identity does not match planning state.")
    if proposal.mode is not mode:
        raise V2PlanPersistenceError("Proposal mode does not match planning state.")
    if not proposal.fingerprint:
        raise V2PlanPersistenceError("Proposal fingerprint is required.")
    existing = session.scalar(select(V2Plan).where(V2Plan.reclear_week_id == state.week_id))
    if existing is not None:
        if existing.fingerprint == proposal.fingerprint:
            return load_persisted_plan_v2(session, existing.id)
        raise V2PlanPersistenceError("A different active V2 proposal already exists for this week.")
        raise V2PlanPersistenceError(
            "A legacy plan already exists for this week; use the legacy boundary."
        )
    current_state = load_planning_state(session, state.static_id, state.week_id)
    supplied_state_fingerprint = planning_state_fingerprint(state)
    current_state_fingerprint = planning_state_fingerprint(current_state)
    if supplied_state_fingerprint != current_state_fingerprint:
        raise V2PlanPersistenceError(
            "Planning state changed after proposal generation; regenerate the proposal."
        )
    _validate_proposal_state(state, proposal, mode)
    try:
        with session.begin_nested():
            plan = V2Plan(
                static_id=state.static_id,
                reclear_week_id=state.week_id,
                mode=mode.value,
                week_number=state.week_number,
                fingerprint=proposal.fingerprint,
                state_fingerprint=supplied_state_fingerprint,
                warnings_json=json.dumps(proposal.warnings),
                score_json=_score_json(getattr(proposal, "score", None)),
                partitions_evaluated=getattr(proposal, "partitions_evaluated", 0),
                actor_id=actor_id,
            )
            session.add(plan)
            session.flush()
            if mode is ClearMode.REGULAR:
                runs = [(1, "Regular")]
                assignment_rows = ((1, proposal.assignments),)
                unassigned = ((1, proposal.unassigned),)
            else:
                runs = [
                    (group.group_number, f"Run {group.group_number}") for group in proposal.groups
                ]
                assignment_rows = tuple(
                    (group.group_number, group.assignments) for group in proposal.groups
                )
                unassigned = tuple((row.group_number, (row,)) for row in proposal.unassigned)
            run_map = {}
            for number, name in runs:
                source_group_id = (
                    proposal.groups[number - 1].group_id if mode is ClearMode.SPLIT else None
                )
                run = V2PlanRun(
                    plan=plan,
                    run_number=number,
                    name=name,
                    source_group_id=source_group_id,
                )
                session.add(run)
                session.flush()
                run_map[number] = run
            if mode is ClearMode.REGULAR:
                participants = state.mains
            else:
                participants = {row.character_id: row for row in (*state.mains, *state.alts)}
            for number, rows in assignment_rows:
                run = run_map[number]
                ids = (
                    proposal.groups[number - 1].participant_ids
                    if mode is ClearMode.SPLIT
                    else tuple(row.character_id for row in participants)
                )
                for index, character_id in enumerate(ids, 1):
                    character = (
                        participants[character_id]
                        if mode is ClearMode.SPLIT
                        else next(row for row in participants if row.character_id == character_id)
                    )
                    session.add(
                        V2PlanParticipant(
                            run=run,
                            character_id=character_id,
                            designation=character.kind.value,
                            sort_order=index,
                        )
                    )
                for index, row in enumerate(rows, 1):
                    _add_assignment(session, plan, run, row, index)
            index = 0
            for number, rows in unassigned:
                for row in rows:
                    index += 1
                    session.add(
                        V2PlanUnassigned(
                            plan=plan,
                            run_number=number,
                            group_id=(number if mode is ClearMode.REGULAR else row.group_id),
                            sort_order=index,
                            floor_number=row.floor_number,
                            loot_key=row.loot_type if mode is ClearMode.REGULAR else row.loot_key,
                            primary_slot=(
                                row.gear_slot.value
                                if mode is ClearMode.REGULAR and row.gear_slot
                                else row.primary_slot.value
                                if mode is ClearMode.SPLIT and row.primary_slot
                                else None
                            ),
                            material_key=row.material_type
                            if mode is ClearMode.REGULAR
                            else row.material_key,
                            reason=row.reason,
                        )
                    )
            session.flush()
    except Exception as error:
        raise V2PlanPersistenceError("The V2 proposal could not be persisted.") from error
    return PersistedV2Plan(plan.id, proposal)


def _validate_proposal_state(state, proposal, mode):
    if mode is ClearMode.REGULAR and not proposal.assignments and not proposal.unassigned:
        raise V2PlanPersistenceError("Regular proposal contains no result rows.")
    if mode is ClearMode.SPLIT:
        if len(proposal.groups) != 2 or any(
            len(group.participant_ids) != 8 for group in proposal.groups
        ):
            raise V2PlanPersistenceError(
                "Split proposal must contain two generated groups of eight."
            )
        character_ids = {row.character_id for row in (*state.mains, *state.alts)}
        if any(
            character_id not in character_ids
            for group in proposal.groups
            for character_id in group.participant_ids
        ):
            raise V2PlanPersistenceError(
                "Proposal contains a character outside the planning state."
            )
        assigned_ids = {
            row.recipient_id
            for group in proposal.groups
            for row in group.assignments
            if row.recipient_id is not None
        }
    else:
        assigned_ids = {row.recipient_id for row in proposal.assignments}
    state_ids = {row.character_id for row in (*state.mains, *state.alts)}
    if not assigned_ids <= state_ids:
        raise V2PlanPersistenceError("Proposal recipient is outside the planning state.")


def _add_assignment(session, plan, run, row, index):
    effects = row.gear_effects
    loot_key = row.loot_type if hasattr(row, "loot_type") else row.loot_key
    primary_slot = row.primary_slot
    material_key = row.material_type if hasattr(row, "material_type") else row.material_key
    recipient_kind = row.recipient_kind
    reason = row.reason
    recipient_id = row.recipient_id
    assignment = V2PlanAssignment(
        plan=plan,
        run=run,
        sort_order=index,
        floor_number=row.floor_number,
        loot_key=loot_key,
        primary_slot=(primary_slot.value if primary_slot else None),
        material_key=material_key,
        recipient_id=recipient_id,
        recipient_job=row.recipient_job,
        recipient_kind=(recipient_kind.value if recipient_kind else None),
        owned_alt_id=getattr(row, "owned_alt_id", None),
        hierarchy_position=row.hierarchy_position,
        disposition=("ASSIGNED" if row.recipient_id is not None else "FREE_ROLL"),
        resource_quantity=getattr(row, "resource_quantity", 1),
        fairness_count=getattr(row, "fairness_count", 0),
        explanation=reason,
        score_json=_score_json(row.score),
    )
    session.add(assignment)
    session.flush()
    for effect_index, effect in enumerate(effects, 1):
        session.add(
            V2PlanEffect(
                assignment=assignment,
                sort_order=effect_index,
                slot_key=effect.slot_key.value,
                resulting_category=effect.resulting_category.value,
            )
        )


def _regular_assignment(row):
    return RegularAssignment(
        row.floor_number,
        row.loot_key,
        _slot(row.primary_slot),
        row.material_key,
        row.recipient_id,
        row.recipient_job,
        CharacterKind(row.recipient_kind),
        row.hierarchy_position,
        tuple(
            ProposedGearEffect(
                _slot(effect.slot_key), GearClassification(effect.resulting_category)
            )
            for effect in row.effects
        ),
        _score_from_json(row.score_json),
        row.explanation,
    )


def _split_assignment(row, run):
    return SplitAssignment(
        run.source_group_id if run.source_group_id is not None else run.id,
        run.run_number,
        row.floor_number,
        row.loot_key,
        _slot(row.primary_slot),
        row.material_key,
        row.recipient_id,
        row.recipient_job,
        CharacterKind(row.recipient_kind) if row.recipient_kind else None,
        row.owned_alt_id,
        row.hierarchy_position,
        0,
        row.fairness_count,
        tuple(
            ProposedGearEffect(
                _slot(effect.slot_key), GearClassification(effect.resulting_category)
            )
            for effect in row.effects
        ),
        row.resource_quantity,
        _score_from_json(row.score_json),
        row.explanation,
    )


def _slot(value):
    return GearSlotCode(value) if value else None


def _score_json(score):
    if score is None:
        return None
    return json.dumps(asdict(score))


def _score_from_json(value):
    if not value:
        return None
    data = json.loads(value)
    if "main_savage_vector" in data:
        return SplitPartitionScore(
            tuple(data["main_savage_vector"]),
            tuple(data["material_quality"]),
            tuple(data["completed_dps_separation"]),
            data["useful_alt_savage_count"],
            tuple(data["alt_savage_vector"]),
            data["useful_tome_weapon_upgrades"],
            data["canonical_partition_order"],
        )
    return SplitScore(**data) if "designation_priority" in data else RegularScore(**data)
