"""Small neutral value fixtures shared by the V2 planner regression modules."""

from datetime import date

from app.models import (
    CharacterKind,
    ClearMode,
    GearClassification,
    GearSlotCode,
    ReclearWorkflowState,
)
from app.schemas.needs_v2 import (
    CharacterNeedsV2Result,
    NeedsV2MaterialNeed,
    NeedsV2SlotResult,
    NeedsV2Status,
)
from app.schemas.planning_state import PlanningCharacter, PlanningState

SLOTS = tuple(GearSlotCode)


def needs(character_id, *, savage=True, material=False, offhand=False):
    rows = []
    for order, slot in enumerate(SLOTS, 1):
        desired = GearClassification.SAVAGE if savage else None
        status = NeedsV2Status.NEEDS_SAVAGE_DROP if savage else NeedsV2Status.NOT_APPLICABLE
        if material and slot in {GearSlotCode.EARRINGS, GearSlotCode.HEAD}:
            desired = GearClassification.AUGMENTED_TOME
            status = NeedsV2Status.NEEDS_AUGMENTATION
        if slot is GearSlotCode.OFFHAND and not offhand:
            desired = None
            status = NeedsV2Status.NOT_APPLICABLE
        rows.append(
            NeedsV2SlotResult(
                character_id,
                1,
                character_id,
                f"J{character_id}",
                character_id,
                order,
                slot,
                slot.value,
                order,
                desired,
                None,
                status,
                1 if savage else None,
                "COFFER" if savage else None,
                GearClassification.TOME
                if material and desired is GearClassification.AUGMENTED_TOME
                else None,
                False,
                -1 if material else None,
                False,
                False,
                "fixture",
                (),
            )
        )
    materials = (
        (
            NeedsV2MaterialNeed(-1, "ACCESSORY_GLAZE", "Glaze", 1, 0, 0, 1, ("EARRINGS",)),
            NeedsV2MaterialNeed(-2, "ARMOR_TWINE", "Twine", 1, 0, 0, 1, ("HEAD",)),
        )
        if material
        else ()
    )
    return CharacterNeedsV2Result(
        character_id,
        f"C{character_id}",
        1,
        "Neutral",
        character_id,
        f"J{character_id}",
        character_id,
        f"BiS {character_id}",
        tuple(rows),
        0,
        len(rows),
        False,
        (),
        materials,
        (),
        (),
        (),
    )


def character(
    character_id,
    member_id,
    kind,
    role="DPS",
    *,
    material=False,
    savage=True,
    offhand=False,
    position=None,
):
    return PlanningCharacter(
        character_id,
        member_id,
        f"C{character_id}",
        "Neutral",
        kind,
        character_id,
        f"J{character_id}",
        offhand,
        role,
        position,
        needs(character_id, savage=savage, material=material, offhand=offhand),
    )


def state(mode=ClearMode.REGULAR, *, mains=None, alts=None, ownership=(), groups=(), fairness=()):
    mains = tuple(
        mains
        or [character(index, index, CharacterKind.MAIN, position=index) for index in range(1, 9)]
    )
    alts = tuple(alts or [])
    return PlanningState(
        1,
        "Neutral",
        1,
        35,
        date(2026, 8, 24),
        ReclearWorkflowState.DRAFT,
        mode,
        date(2026, 8, 24),
        mains,
        alts,
        tuple(ownership),
        tuple(groups),
        (),
        (),
        tuple((row.job_id, row.job_abbreviation, row.hierarchy_position or 99) for row in mains),
        None,
        tuple(fairness),
        (),
    )


def split_state():
    mains = tuple(
        character(
            index,
            index,
            CharacterKind.MAIN,
            ("TANK", "TANK", "HEALER", "HEALER", "DPS", "DPS", "DPS", "DPS")[index - 1],
            position=index,
        )
        for index in range(1, 9)
    )
    alts = tuple(
        character(
            index + 8,
            index,
            CharacterKind.ALT,
            ("TANK", "TANK", "HEALER", "HEALER", "DPS", "DPS", "DPS", "DPS")[index - 1],
            position=index,
        )
        for index in range(1, 9)
    )
    alts = tuple(
        row.__class__(
            row.character_id,
            row.member_id,
            row.name,
            row.world,
            row.kind,
            row.member_id,
            f"J{row.member_id}",
            row.uses_offhand,
            row.combat_role,
            row.hierarchy_position,
            needs(row.character_id, offhand=row.uses_offhand),
        )
        for row in alts
    )
    return state(
        ClearMode.SPLIT, mains=mains, alts=alts, ownership=tuple((i, i + 8) for i in range(1, 9))
    )
