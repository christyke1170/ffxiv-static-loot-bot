"""Relative average-item-level calculation from category-only gear state."""

from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.models import (
    Character,
    CharacterGearSlot,
    GearClassification,
    GearSlotCode,
    Static,
    StaticMember,
)
from app.schemas.item_level import CharacterItemLevelResult, SlotItemLevel

AVERAGE_SLOTS = (
    GearSlotCode.HEAD,
    GearSlotCode.BODY,
    GearSlotCode.HANDS,
    GearSlotCode.LEGS,
    GearSlotCode.FEET,
    GearSlotCode.EARRINGS,
    GearSlotCode.NECKLACE,
    GearSlotCode.BRACELETS,
    GearSlotCode.RING_1,
    GearSlotCode.RING_2,
)


def calculate_slot_item_level(
    baseline: int, category: GearClassification, slot: GearSlotCode
) -> int | None:
    if category is GearClassification.CRAFTED_EX:
        return baseline
    if category is GearClassification.TOME:
        return baseline + 10
    if category is GearClassification.AUGMENTED_TOME:
        return baseline + 20
    if category is GearClassification.SAVAGE:
        return baseline + (25 if slot in {GearSlotCode.WEAPON, GearSlotCode.OFFHAND} else 20)
    return None


def calculate_character_item_level(session, character_id: int) -> CharacterItemLevelResult:
    character = session.scalar(
        select(Character)
        .where(Character.id == character_id)
        .options(
            joinedload(Character.job),
            joinedload(Character.static_member).joinedload(StaticMember.static),
            selectinload(Character.gear_slots).joinedload(CharacterGearSlot.gear_slot),
        )
    )
    if character is None:
        raise LookupError(f"unknown character id {character_id}")
    return _calculate(character, character.static_member.static)


def calculate_roster_item_levels(session, static_id: int) -> dict[int, CharacterItemLevelResult]:
    static = session.scalar(
        select(Static)
        .where(Static.id == static_id)
        .options(
            selectinload(Static.members)
            .selectinload(StaticMember.characters)
            .options(
                joinedload(Character.job),
                selectinload(Character.gear_slots).joinedload(CharacterGearSlot.gear_slot),
            )
        )
    )
    if static is None:
        raise LookupError(f"unknown static id {static_id}")
    characters = [character for member in static.members for character in member.characters]
    return {character.id: _calculate(character, static) for character in characters}


def _calculate(character: Character, static: Static) -> CharacterItemLevelResult:
    baseline = static.crafted_item_level
    by_code = {row.gear_slot.code: row for row in character.gear_slots}
    expected = (GearSlotCode.WEAPON, *AVERAGE_SLOTS)
    if character.job.uses_offhand:
        expected = (GearSlotCode.WEAPON, GearSlotCode.OFFHAND, *AVERAGE_SLOTS)
    garbage = tuple(
        code
        for code in expected
        if code in by_code and by_code[code].current_classification is GearClassification.GARBAGE
    )
    missing = tuple(
        code
        for code in expected
        if code not in by_code
        or by_code[code].current_classification is GearClassification.NOT_APPLICABLE
    )
    if not character.job.uses_offhand and (
        GearSlotCode.OFFHAND not in by_code
        or by_code[GearSlotCode.OFFHAND].current_classification
        is not GearClassification.NOT_APPLICABLE
    ):
        missing = (*missing, GearSlotCode.OFFHAND)
    all_codes = tuple(GearSlotCode)
    slots = tuple(
        SlotItemLevel(
            code,
            (
                by_code[code].gear_slot.display_name
                if code in by_code
                else code.value.replace("_", " ").title()
            ),
            by_code[code].current_classification if code in by_code else None,
            (
                calculate_slot_item_level(baseline, by_code[code].current_classification, code)
                if baseline is not None
                and code in by_code
                and by_code[code].current_classification
                not in {GearClassification.GARBAGE, GearClassification.NOT_APPLICABLE}
                else None
            ),
        )
        for code in all_codes
    )
    warnings = []
    if baseline is None:
        warnings.append("Static crafted item level is not configured.")
    if garbage:
        warnings.append(
            "Gear replacement required immediately: "
            + ", ".join(code.value.replace("_", " ").title() for code in garbage)
        )
    if missing:
        warnings.append(
            "Missing or invalid applicable gear: "
            + ", ".join(code.value.replace("_", " ").title() for code in missing)
        )
    valid = baseline is not None and not garbage and not missing
    values = {row.slot: row.calculated_item_level for row in slots}
    weapon = None
    exact = None
    displayed = None
    if valid:
        weapon = Decimal(values[GearSlotCode.WEAPON])
        if character.job.uses_offhand:
            weapon = (weapon + Decimal(values[GearSlotCode.OFFHAND])) / Decimal(2)
        exact = (weapon + sum(Decimal(values[code]) for code in AVERAGE_SLOTS)) / Decimal(11)
        displayed = int(exact.to_integral_value(rounding=ROUND_FLOOR))
    return CharacterItemLevelResult(
        character.id,
        character.name,
        static.id,
        baseline,
        character.job.abbreviation,
        character.job.uses_offhand,
        slots,
        weapon,
        exact,
        displayed,
        valid,
        garbage,
        missing,
        tuple(warnings),
    )
