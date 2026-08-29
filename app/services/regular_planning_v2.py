"""Pure tier-neutral Regular loot proposal calculation."""

from hashlib import sha256

from app.domain import loot_rules
from app.models import ClearMode, GearClassification, GearSlotCode
from app.schemas.needs_v2 import NeedsV2Status
from app.schemas.planning_state import PlanningState
from app.schemas.regular_planning_v2 import (
    ProposedGearEffect,
    RegularAssignment,
    RegularPlanProposal,
    RegularScore,
    UnassignedRegularLoot,
)


class RegularPlanningV2Error(ValueError):
    """The neutral state cannot safely produce a Regular proposal."""


def generate_regular_plan_v2(state: PlanningState) -> RegularPlanProposal:
    """Generate a deterministic, immutable Regular proposal without I/O."""
    if not isinstance(state, PlanningState):
        raise TypeError("state must be a PlanningState")
    if state.mode is not ClearMode.REGULAR:
        raise RegularPlanningV2Error("Regular V2 planning requires Regular planning state.")
    warnings = list(state.warnings)
    if not state.mains:
        raise RegularPlanningV2Error("Regular V2 planning requires at least one active Main.")
    if not state.hierarchy:
        warnings.append("No hierarchy snapshot is available; roster order is used.")
    hierarchy = {job_id: position for job_id, _, position in state.hierarchy}
    mains = tuple(sorted(state.mains, key=lambda row: (row.member_id, row.character_id)))
    assignments: list[RegularAssignment] = []
    unassigned: list[UnassignedRegularLoot] = []
    used_slots: set[tuple[int, GearSlotCode]] = set()
    assignment_counts: dict[int, int] = {}
    for drop in loot_rules.REGULAR_DROPS:
        candidates = _candidates(state, mains, drop, hierarchy, used_slots, assignment_counts)
        if not candidates:
            unassigned.append(
                UnassignedRegularLoot(
                    drop.floor,
                    drop.loot_type,
                    drop.slot,
                    drop.material_type,
                    "No eligible Main has a matching incomplete V2 need.",
                )
            )
            continue
        character, score, category, explanation, effects, primary_slot = min(
            candidates, key=lambda item: item[1].comparison_key
        )
        assignments.append(
            RegularAssignment(
                drop.floor,
                drop.loot_type,
                primary_slot,
                drop.material_type,
                character.character_id,
                character.job_abbreviation,
                character.kind,
                character.hierarchy_position,
                effects,
                score,
                explanation,
            )
        )
        if drop.slot is not None:
            used_slots.add((character.character_id, primary_slot))
        assignment_counts[character.character_id] = (
            assignment_counts.get(character.character_id, 0) + 1
        )
    proposal_data = (
        state.static_id,
        state.week_id,
        state.week_number,
        state.mode.value,
        tuple(assignments),
        tuple(unassigned),
        tuple(dict.fromkeys(warnings)),
    )
    fingerprint = sha256(repr(proposal_data).encode()).hexdigest()
    return RegularPlanProposal(
        state.static_id,
        state.week_id,
        state.week_number,
        state.mode,
        fingerprint,
        tuple(assignments),
        tuple(unassigned),
        tuple(dict.fromkeys(warnings)),
    )


def _candidates(state, mains, drop, hierarchy, used_slots, assignment_counts):
    result = []
    for character in mains:
        if not _eligible_for_floor(state, character.character_id, drop.floor):
            continue
        needs = character.needs
        if needs is None or needs.configuration_warnings:
            continue
        if drop.slot is not None:
            eligible_slots = (
                (drop.slot, GearSlotCode.OFFHAND)
                if drop.slot is GearSlotCode.WEAPON and character.uses_offhand
                else (drop.slot,)
            )
            rows = [
                row
                for row in needs.slot_results
                if row.gear_slot in eligible_slots
                and row.desired is GearClassification.SAVAGE
                and row.status
                in {NeedsV2Status.NEEDS_SAVAGE_DROP, NeedsV2Status.OWNED_COFFER_AVAILABLE}
                and all((character.character_id, slot) not in used_slots for slot in eligible_slots)
            ]
            category = GearClassification.SAVAGE
        else:
            rows = _material_rows(needs, drop.material_type)
            category = GearClassification.AUGMENTED_TOME
        if not rows:
            continue
        position = character.hierarchy_position or max(hierarchy.values(), default=0) + 1
        history = next(
            (row for row in state.fairness if row.character_id == character.character_id),
            None,
        )
        receipt_count = history.savage_receipts if history else 0
        material_count = dict(history.material_grants).get(drop.material_type, 0) if history else 0
        fairness_count = material_count if drop.material_type else receipt_count
        score = RegularScore(
            position,
            assignment_counts.get(character.character_id, 0),
            fairness_count if not drop.material_type else 0,
            material_count if drop.material_type else 0,
            character.member_id,
            character.character_id,
        )
        effects = (
            tuple(
                ProposedGearEffect(slot, GearClassification.SAVAGE)
                for slot in (GearSlotCode.WEAPON, GearSlotCode.OFFHAND)
            )
            if drop.slot is GearSlotCode.WEAPON and character.uses_offhand
            else (
                (ProposedGearEffect(drop.slot, GearClassification.SAVAGE),)
                if drop.slot is not None
                else ()
            )
        )
        primary_slot = min(rows, key=lambda row: row.sort_order).gear_slot if drop.slot else None
        result.append(
            (
                character,
                score,
                category,
                f"Selected hierarchy position {position}; historical fairness count "
                f"{fairness_count}; matching incomplete {category.value} need.",
                effects,
                primary_slot,
            )
        )
    return result


def _material_rows(needs, material_type):
    required = next(
        (row for row in needs.material_needs if row.material_code == material_type), None
    )
    if required is None or required.additional_needed <= 0:
        return []
    return [
        row
        for row in needs.slot_results
        if row.desired is GearClassification.AUGMENTED_TOME
        and row.status in {NeedsV2Status.NEEDS_BASE_TOME, NeedsV2Status.NEEDS_AUGMENTATION}
        and row.slot_name in required.slot_names
    ]


def _eligible_for_floor(state, character_id, floor_number):
    rows = [
        row
        for row in state.lockouts
        if row.character_id == character_id and row.floor_number == floor_number
    ]
    return not rows or all(row.loot_eligible and not row.cleared for row in rows)
