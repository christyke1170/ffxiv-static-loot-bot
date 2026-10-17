"""Shared pure rules for greedy coffers and fair Main-only material grants."""

from app.models import GearClassification, GearSlotCode
from app.schemas.needs_v2 import NeedsV2Status
from app.schemas.regular_planning_v2 import ProposedGearEffect


def hierarchy_position(state, character):
    return next(
        (position for job_id, _, position in state.hierarchy if job_id == character.job_id),
        max((row[2] for row in state.hierarchy), default=0) + 1,
    )


def hierarchy_key(state, character):
    # IDs only stabilize otherwise identical job priorities; they never offset hierarchy.
    return hierarchy_position(state, character), character.member_id, character.character_id


def floor_eligible(state, character_id, floor_number):
    return all(
        row.loot_eligible and not row.cleared
        for row in state.lockouts
        if row.character_id == character_id and row.floor_number == floor_number
    )


def savage_need(character, drop, used):
    slots = (
        (GearSlotCode.WEAPON, GearSlotCode.OFFHAND)
        if drop.slot is GearSlotCode.WEAPON and character.uses_offhand
        else (GearSlotCode.RING_1, GearSlotCode.RING_2)
        if drop.slot is GearSlotCode.RING_1
        else (drop.slot,)
    )
    rows = [
        row
        for row in character.needs.slot_results
        if row.gear_slot in slots
        and (character.character_id, row.gear_slot) not in used
        and row.desired is GearClassification.SAVAGE
        and row.status in {NeedsV2Status.NEEDS_SAVAGE_DROP, NeedsV2Status.OWNED_COFFER_AVAILABLE}
    ]
    if not rows:
        return None
    primary = min(rows, key=lambda row: row.sort_order).gear_slot
    effects = tuple(
        ProposedGearEffect(slot, GearClassification.SAVAGE)
        for slot in (slots if drop.slot is GearSlotCode.WEAPON else (primary,))
    )
    return primary, effects


def material_priority(state, character, material, planned):
    needs = character.needs
    required = next((row for row in needs.material_needs if row.material_code == material), None)
    if required is None or required.additional_needed <= planned.get(
        (character.character_id, material), 0
    ):
        return None
    if not any(
        row.desired is GearClassification.AUGMENTED_TOME
        and row.status in {NeedsV2Status.NEEDS_BASE_TOME, NeedsV2Status.NEEDS_AUGMENTATION}
        and row.slot_name in required.slot_names
        for row in needs.slot_results
    ):
        return None
    history = next(
        (row for row in state.fairness if row.character_id == character.character_id), None
    )
    grants = dict(history.material_grants) if history else {}
    combined = sum(grants.values()) + sum(
        quantity
        for (identifier, _), quantity in planned.items()
        if identifier == character.character_id
    )
    specific = grants.get(material, 0) + planned.get((character.character_id, material), 0)
    remaining = required.additional_needed - planned.get((character.character_id, material), 0)
    return (
        combined,
        -remaining,
        hierarchy_position(state, character),
        specific,
        character.member_id,
        character.character_id,
    )
