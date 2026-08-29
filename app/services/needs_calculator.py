"""Pure needs calculation over immutable needs-state values."""

from collections import defaultdict

from app.models import GearClassification, GearSlotCode
from app.schemas.needs_v2 import (
    CharacterNeedsV2Result,
    NeedsV2BookBalance,
    NeedsV2CofferSummary,
    NeedsV2MaterialNeed,
    NeedsV2SavageNeed,
    NeedsV2SlotResult,
    NeedsV2Status,
)
from app.services.needs_state import CharacterNeedsState


def _rule_for(state, slot):
    if slot.required_floor_number is None or slot.required_loot_type_id is None:
        return None
    floor = next(
        (row for row in state.floors if row.floor_number == slot.required_floor_number), None
    )
    return (
        next((row for row in floor.rules if row.loot_type_id == slot.required_loot_type_id), None)
        if floor
        else None
    )


def _loot_exists(state, loot_id):
    return any(row.loot_type_id == loot_id for row in state.loot_types)


def calculate_needs_from_state(state: CharacterNeedsState) -> CharacterNeedsV2Result:
    """Calculate needs without sessions, database access, writes, or mutation."""
    if not isinstance(state, CharacterNeedsState):
        raise TypeError("state must be a CharacterNeedsState")
    owned = dict(state.material_quantities)
    coffers = defaultdict(int)
    categorized = defaultdict(int)
    for item in state.inventory:
        if item.quantity <= 0:
            continue
        if item.loot_type_id is not None:
            coffers[item.loot_type_id] += item.quantity
        elif item.slot_id is not None and item.category is not None:
            categorized[(item.slot_id, item.category)] += item.quantity
    coffer_used = defaultdict(int)
    material_used = defaultdict(int)
    rows = []
    warnings = list(state.warnings)
    for slot in sorted(state.slots, key=lambda row: row.sort_order):
        row, row_warnings = _slot(
            state, slot, owned, categorized, coffers, coffer_used, material_used
        )
        rows.append(row)
        warnings.extend(row_warnings)
    return _result(state, rows, warnings)


def _slot(state, slot, owned, categorized, coffers, coffer_used, material_used):
    desired = slot.desired
    status = NeedsV2Status.INVALID_CONFIGURATION
    explanation = "Missing desired category configuration."
    warnings = []
    base_owned = False
    material_available = False
    coffer_allocated = False
    if desired is GearClassification.GARBAGE:
        warnings.append("GARBAGE is not a valid desired category.")
    elif desired is None:
        warnings.append("Missing desired category configuration.")
    elif (
        slot.slot is GearSlotCode.OFFHAND
        and not state.uses_offhand
        and desired is not GearClassification.NOT_APPLICABLE
    ):
        warnings.append("Offhand must be NOT_APPLICABLE for jobs without Offhand capability.")
    elif desired is GearClassification.NOT_APPLICABLE:
        status, explanation = NeedsV2Status.NOT_APPLICABLE, "This slot is not applicable."
    elif slot.manually_complete:
        status, explanation = NeedsV2Status.MANUALLY_COMPLETE, "This slot is manually complete."
    elif slot.current is desired:
        status, explanation = (
            NeedsV2Status.COMPLETE,
            "Current category exactly matches the desired category.",
        )
    elif desired is GearClassification.SAVAGE:
        status, explanation = (
            NeedsV2Status.NEEDS_SAVAGE_DROP,
            "The configured Savage drop is still needed.",
        )
        if (
            slot.required_loot_type_id is not None
            and coffers[slot.required_loot_type_id] > coffer_used[slot.required_loot_type_id]
        ):
            coffer_used[slot.required_loot_type_id] += 1
            coffer_allocated = True
            status, explanation = (
                NeedsV2Status.OWNED_COFFER_AVAILABLE,
                "A matching unopened coffer is available.",
            )
    elif desired is GearClassification.AUGMENTED_TOME:
        base_owned = (
            slot.current is GearClassification.TOME
            or categorized[(slot.slot_id, GearClassification.TOME)] > 0
        )
        if not base_owned:
            status, explanation = (
                NeedsV2Status.NEEDS_BASE_TOME,
                "The base Tome category is required.",
            )
        elif (
            slot.required_material_type_id is not None
            and owned.get(slot.required_material_type_id, 0)
            > material_used[slot.required_material_type_id]
        ):
            material_used[slot.required_material_type_id] += 1
            material_available = True
            status, explanation = (
                NeedsV2Status.READY_TO_AUGMENT,
                "The base Tome and augmentation material are available.",
            )
        else:
            status, explanation = (
                NeedsV2Status.NEEDS_AUGMENTATION,
                "The base Tome is owned but augmentation material is needed.",
            )

    if desired is GearClassification.SAVAGE and status not in {
        NeedsV2Status.INVALID_CONFIGURATION,
        NeedsV2Status.MANUALLY_COMPLETE,
        NeedsV2Status.COMPLETE,
    }:
        if slot.required_floor_number is None:
            warnings.append("Missing floor configuration.")
        if slot.required_loot_type_id is None or not _loot_exists(
            state, slot.required_loot_type_id
        ):
            warnings.append("Missing loot type configuration.")
        if _rule_for(state, slot) is None and slot.required_loot_type_id is not None:
            warnings.append("Missing floor loot rule configuration.")
    elif (
        desired is GearClassification.AUGMENTED_TOME
        and status
        not in {
            NeedsV2Status.INVALID_CONFIGURATION,
            NeedsV2Status.MANUALLY_COMPLETE,
            NeedsV2Status.COMPLETE,
        }
        and (
            slot.required_material_type_id is None
            or not any(
                row.material_type_id == slot.required_material_type_id for row in state.materials
            )
        )
    ):
        warnings.append("Missing augmentation-material configuration.")
    if (
        desired is GearClassification.AUGMENTED_TOME
        and status
        not in {
            NeedsV2Status.INVALID_CONFIGURATION,
            NeedsV2Status.MANUALLY_COMPLETE,
            NeedsV2Status.COMPLETE,
        }
        and (
            slot.required_material_type_id is None
            or not any(
                row.material_type_id == slot.required_material_type_id for row in state.materials
            )
        )
    ):
        status, explanation = NeedsV2Status.INVALID_CONFIGURATION, "Configuration is incomplete."
    if (
        desired is GearClassification.SAVAGE
        and warnings
        and status
        not in {
            NeedsV2Status.MANUALLY_COMPLETE,
            NeedsV2Status.COMPLETE,
        }
    ):
        status, explanation = NeedsV2Status.INVALID_CONFIGURATION, "Configuration is incomplete."

    return NeedsV2SlotResult(
        state.character_id,
        state.static_id,
        state.job_id,
        state.job_abbreviation,
        state.bis_set_id,
        slot.slot_id,
        slot.slot,
        slot.display_name,
        slot.sort_order,
        desired,
        slot.current,
        status,
        slot.required_floor_number,
        slot.required_loot_type_code,
        GearClassification.TOME if desired is GearClassification.AUGMENTED_TOME else None,
        base_owned,
        slot.required_material_type_id,
        material_available,
        coffer_allocated,
        explanation,
        tuple(warnings),
    ), warnings


def _result(state, rows, warnings):
    savage = defaultdict(list)
    materials = defaultdict(list)
    coffers = defaultdict(list)
    for row in rows:
        if (
            row.status in {NeedsV2Status.NEEDS_SAVAGE_DROP, NeedsV2Status.OWNED_COFFER_AVAILABLE}
            and row.required_floor_number
            and row.required_loot_type_code
        ):
            savage[(row.required_floor_number, row.required_loot_type_code)].append(row)
        if (
            row.desired is GearClassification.AUGMENTED_TOME
            and row.required_material_type_id is not None
        ):
            materials[row.required_material_type_id].append(row)
        if row.coffer_allocated and row.required_loot_type_code:
            coffers[row.required_loot_type_code].append(row)
    complete = sum(
        row.status in {NeedsV2Status.COMPLETE, NeedsV2Status.MANUALLY_COMPLETE} for row in rows
    )
    applicable = sum(row.status is not NeedsV2Status.NOT_APPLICABLE for row in rows)
    return CharacterNeedsV2Result(
        state.character_id,
        state.character_name,
        state.static_id,
        state.static_name,
        state.job_id,
        state.job_abbreviation,
        state.bis_set_id,
        state.bis_set_name,
        tuple(rows),
        complete,
        applicable,
        complete == applicable,
        tuple(
            NeedsV2SavageNeed(floor, code, len(group), tuple(row.slot_name for row in group))
            for (floor, code), group in sorted(savage.items())
        ),
        tuple(
            NeedsV2MaterialNeed(
                mid,
                next(
                    (item.code for item in state.materials if item.material_type_id == mid),
                    str(mid),
                ),
                next(
                    (item.name for item in state.materials if item.material_type_id == mid),
                    str(mid),
                ),
                len(group),
                dict(state.material_quantities).get(mid, 0),
                sum(row.material_available for row in group),
                max(0, len(group) - sum(row.material_available for row in group)),
                tuple(row.slot_name for row in group),
            )
            for mid, group in sorted(materials.items())
        ),
        tuple(NeedsV2BookBalance(floor, available) for floor, available in sorted(state.books)),
        tuple(
            NeedsV2CofferSummary(
                code,
                sum(
                    item.quantity
                    for item in state.inventory
                    if item.loot_type_id
                    == next((x.loot_type_id for x in state.loot_types if x.code == code), None)
                ),
                len(group),
                tuple(row.slot_name for row in group),
            )
            for code, group in sorted(coffers.items())
        ),
        tuple(dict.fromkeys(warnings)),
    )
