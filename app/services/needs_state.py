"""Immutable neutral inputs for the V2 needs calculator."""

from dataclasses import dataclass

from sqlalchemy import select

from app.domain import loot_rules
from app.models import (
    BisSet,
    Character,
    CharacterGearSlot,
    GearClassification,
    GearSlot,
    GearSlotCode,
    LootCategory,
    V2ResourceBalance,
)


@dataclass(frozen=True, slots=True)
class NeedsSlotState:
    slot_id: int
    slot: GearSlotCode
    display_name: str
    sort_order: int
    desired: GearClassification | None
    current: GearClassification | None
    manually_complete: bool
    required_floor_number: int | None = None
    required_loot_type_id: int | None = None
    required_loot_type_code: str | None = None
    required_material_type_id: int | None = None
    required_material_type_code: str | None = None


@dataclass(frozen=True, slots=True)
class NeedsFloorRuleState:
    loot_type_id: int
    loot_code: str
    loot_name: str
    loot_category: LootCategory
    expected_quantity: int
    material_type_id: int | None


@dataclass(frozen=True, slots=True)
class NeedsFloorState:
    floor_id: int
    floor_number: int
    name: str
    rules: tuple[NeedsFloorRuleState, ...]


@dataclass(frozen=True, slots=True)
class NeedsLootTypeState:
    loot_type_id: int
    code: str
    name: str
    category: LootCategory


@dataclass(frozen=True, slots=True)
class NeedsMaterialTypeState:
    material_type_id: int
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class NeedsInventoryState:
    inventory_id: int
    slot_id: int | None
    category: GearClassification | None
    loot_type_id: int | None
    quantity: int


@dataclass(frozen=True, slots=True)
class CharacterNeedsState:
    character_id: int | None
    character_name: str | None
    static_id: int | None
    static_name: str | None
    job_id: int | None
    job_abbreviation: str | None
    uses_offhand: bool
    bis_set_id: int | None
    bis_set_name: str | None
    slots: tuple[NeedsSlotState, ...]
    floors: tuple[NeedsFloorState, ...]
    loot_types: tuple[NeedsLootTypeState, ...]
    materials: tuple[NeedsMaterialTypeState, ...]
    books: tuple[tuple[int, int], ...]
    material_quantities: tuple[tuple[int, int], ...]
    inventory: tuple[NeedsInventoryState, ...]
    warnings: tuple[str, ...] = ()


_FIXED_CODES = tuple(
    sorted(
        {
            code
            for slot in GearSlotCode
            for code in (
                loot_rules.savage_loot_type(slot),
                loot_rules.augmentation_material_type(slot),
            )
            if code
        }
        | {
            "ACCESSORY_COFFER",
            "HEAD_COFFER",
            "GLOVES_COFFER",
            "BOOTS_COFFER",
            "CHEST_COFFER",
            "PANTS_COFFER",
            "WEAPON_COFFER",
        }
    )
)
_MATERIAL_CODES = ("ACCESSORY_GLAZE", "ARMOR_TWINE")


def load_character_needs_state(session, character_id: int) -> CharacterNeedsState:
    return load_characters_needs_states(session, (character_id,))[0]


def load_characters_needs_states(session, character_ids) -> tuple[CharacterNeedsState, ...]:
    ordered = tuple(dict.fromkeys(character_ids))
    if not ordered:
        return ()
    characters = {
        row.id: row
        for row in session.scalars(
            select(Character).where(Character.id.in_(ordered)).order_by(Character.id)
        )
    }
    result = []
    for character_id in ordered:
        character = characters.get(character_id)
        if character is None:
            raise LookupError(f"Character {character_id} was not found.")
        result.append(_load_one(session, character))
    return tuple(result)


def _load_one(session, character: Character) -> CharacterNeedsState:
    static = character.static_member.static
    bis_set = session.scalar(
        select(BisSet).where(
            BisSet.static_id == static.id,
            BisSet.job_id == character.job_id,
            BisSet.active.is_(True),
        )
    )
    slots = {
        row.gear_slot_id: row
        for row in session.scalars(
            select(CharacterGearSlot).where(CharacterGearSlot.character_id == character.id)
        )
    }
    bis_items = {row.gear_slot_id: row for row in bis_set.items} if bis_set else {}
    warnings = (
        []
        if bis_set
        else [
            f"Character {character.name} has no Static + Job BiS for {character.job.abbreviation}."
        ]
    )
    ids = {code: -(index + 1) for index, code in enumerate(_FIXED_CODES)}
    material_ids = {code: -(index + 1) for index, code in enumerate(_MATERIAL_CODES)}
    balances = {
        row.resource_key: row.quantity
        for row in session.scalars(
            select(V2ResourceBalance).where(
                V2ResourceBalance.static_id == static.id,
                V2ResourceBalance.recipient_id == character.id,
            )
        )
    }
    slot_states = []
    gear_slots = session.scalars(select(GearSlot).order_by(GearSlot.sort_order)).all()
    for gear_slot in gear_slots:
        item = bis_items.get(gear_slot.id)
        current = slots.get(gear_slot.id)
        desired = item.classification if item else None
        if desired is GearClassification.GARBAGE:
            warnings.append(f"{gear_slot.display_name}: GARBAGE is not a valid desired category.")
        floor = None
        code = None
        material_code = None
        if desired is GearClassification.SAVAGE:
            floor = loot_rules.floor_for_savage(gear_slot.code)
            code = loot_rules.savage_loot_type(gear_slot.code)
        elif desired is GearClassification.AUGMENTED_TOME:
            rule = loot_rules.augmentation_rule(gear_slot.code)
            floor = rule.floor if rule else None
            material_code = rule.material_type if rule else None
            code = "TOME_WEAPON" if gear_slot.code is GearSlotCode.WEAPON else None
        slot_states.append(
            NeedsSlotState(
                gear_slot.id,
                gear_slot.code,
                gear_slot.display_name,
                gear_slot.sort_order,
                desired,
                current.current_classification if current else None,
                current.manually_complete if current else False,
                floor,
                ids.get(code),
                code,
                material_ids.get(material_code),
                material_code,
            )
        )
    floors = tuple(
        NeedsFloorState(
            -number,
            number,
            f"Floor {number}",
            tuple(
                NeedsFloorRuleState(
                    ids[code],
                    code,
                    code.replace("_", " ").title(),
                    loot_rules.loot_category(code),
                    1,
                    material_ids.get(code),
                )
                for code in _FIXED_CODES
                if (
                    (code.startswith("BOOK_") and int(code.rsplit("_", 1)[-1]) == number)
                    or (
                        code in {loot_rules.savage_loot_type(slot) for slot in GearSlotCode}
                        and any(
                            loot_rules.floor_for_savage(slot) == number
                            and loot_rules.savage_loot_type(slot) == code
                            for slot in GearSlotCode
                        )
                    )
                    or loot_rules.floor_for_material(code) == number
                )
            ),
        )
        for number in loot_rules.floors()
    )
    loot_types = tuple(
        NeedsLootTypeState(
            ids[code], code, code.replace("_", " ").title(), loot_rules.loot_category(code)
        )
        for code in _FIXED_CODES
    )
    materials = tuple(
        NeedsMaterialTypeState(material_ids[code], code, code.replace("_", " ").title())
        for code in _MATERIAL_CODES
    )
    books = tuple(
        (number, balances.get(f"BOOK_FLOOR_{number}", 0)) for number in loot_rules.floors()
    )
    material_quantities = tuple(
        (material_ids[code], balances.get(code, 0)) for code in _MATERIAL_CODES
    )
    inventory = tuple(
        NeedsInventoryState(index + 1, None, None, ids[code], balances.get(code, 0))
        for index, code in enumerate(_FIXED_CODES)
        if code.endswith("_COFFER") and balances.get(code, 0)
    )
    return CharacterNeedsState(
        character.id,
        character.name,
        static.id,
        static.name,
        character.job.id,
        character.job.abbreviation,
        character.job.uses_offhand,
        bis_set.id if bis_set else None,
        bis_set.name if bis_set else None,
        tuple(slot_states),
        floors,
        loot_types,
        materials,
        books,
        material_quantities,
        inventory,
        tuple(dict.fromkeys(warnings)),
    )
