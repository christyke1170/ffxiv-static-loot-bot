"""Transactional current-gear and character-resource administration."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    AugmentationMaterialType,
    Character,
    CharacterAugmentationInventory,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    GearClassification,
    GearSlot,
    GearSlotCode,
    InventoryItem,
    LootType,
    RaidFloor,
    Static,
)
from app.services.imports import ImportValidationError
from app.services.needs import calculate_character_needs

CURRENT_CLASSIFICATIONS = frozenset(
    {
        GearClassification.CRAFTED_EX,
        GearClassification.SAVAGE,
        GearClassification.TOME,
        GearClassification.AUGMENTED_TOME,
        GearClassification.GARBAGE,
    }
)


@dataclass(frozen=True, slots=True)
class CurrentStateImportCounts:
    characters: int = 0
    gear_slots: int = 0
    inventory_items: int = 0
    book_balances: int = 0
    augmentation_materials: int = 0


def resolve_character(
    session: Session, static: Static, name: str, world: str | None = None
) -> Character:
    statement = select(Character).where(
        Character.name == name,
        Character.static_member.has(static_id=static.id),
    )
    if world is not None:
        statement = statement.where(Character.world == world)
    rows = list(session.scalars(statement))
    if not rows:
        raise ValueError("Character is not in the selected static.")
    if len(rows) > 1:
        raise ValueError("Character name is ambiguous; include the world.")
    return rows[0]


def set_gear(
    session: Session,
    static: Static,
    character: Character,
    slot: GearSlot,
    classification: GearClassification,
    actor_id: int,
) -> CharacterGearSlot:
    _require_character(static, character)
    if classification is GearClassification.NOT_APPLICABLE:
        if slot.code is not GearSlotCode.OFFHAND or character.job.uses_offhand:
            raise ValueError("NOT_APPLICABLE is valid only for a configured non-offhand job.")
    else:
        _require_applicable_slot(character, slot)
        _require_current_classification(classification, slot.code)
    row = _gear_row(session, character.id, slot.id)
    if row is None:
        row = CharacterGearSlot(
            character=character, gear_slot=slot, current_classification=classification
        )
        session.add(row)
    elif row.current_classification is not classification:
        row.manually_complete = False
    row.current_classification = classification
    row.updated_at = datetime.now(UTC)
    session.flush()
    _audit(
        session, static.id, actor_id, "GEAR_SET", "CharacterGearSlot", row.id, classification.value
    )
    return row


def clear_gear(
    session: Session, static: Static, character: Character, slot: GearSlot, actor_id: int
) -> None:
    _require_character(static, character)
    _require_applicable_slot(character, slot)
    row = _gear_row(session, character.id, slot.id)
    if row is None:
        raise ValueError("That gear slot is already empty.")
    row_id = row.id
    session.delete(row)
    _audit(session, static.id, actor_id, "GEAR_CLEARED", "CharacterGearSlot", row_id)


def set_manual_completion(
    session: Session,
    static: Static,
    character: Character,
    slot: GearSlot,
    complete: bool,
    actor_id: int,
    reason: str | None = None,
) -> CharacterGearSlot:
    _require_character(static, character)
    row = _gear_row(session, character.id, slot.id)
    if row is None:
        raise ValueError("Set current gear before applying a manual completion override.")
    row.manually_complete = complete
    row.updated_at = datetime.now(UTC)
    _audit(
        session,
        static.id,
        actor_id,
        "GEAR_MANUAL_COMPLETE" if complete else "GEAR_MANUAL_UNCOMPLETE",
        "CharacterGearSlot",
        row.id,
        reason,
    )
    return row


def set_inventory(
    session: Session,
    static: Static,
    character: Character,
    slot: GearSlot,
    classification: GearClassification,
    quantity: int,
    actor_id: int,
) -> InventoryItem | None:
    _nonnegative(quantity, "Quantity")
    _require_character(static, character)
    _require_applicable_slot(character, slot)
    _require_current_classification(classification, slot.code)
    row = session.scalar(
        select(InventoryItem).where(
            InventoryItem.character_id == character.id,
            InventoryItem.gear_slot_id == slot.id,
            InventoryItem.classification == classification,
        )
    )
    if quantity == 0:
        if row is not None:
            session.delete(row)
    elif row is None:
        row = InventoryItem(
            character=character,
            gear_slot=slot,
            classification=classification,
            quantity=quantity,
        )
        session.add(row)
    else:
        row.quantity = quantity
    _audit(session, static.id, actor_id, "INVENTORY_SET", "Character", character.id, str(quantity))
    return row if quantity else None


def set_loot_resource(
    session: Session,
    static: Static,
    character: Character,
    loot_type: LootType,
    quantity: int,
    actor_id: int,
) -> InventoryItem | None:
    _nonnegative(quantity, "Quantity")
    _require_character(static, character)
    if loot_type.raid_tier_id != static.active_raid_tier_id:
        raise ValueError("Loot resource is not from the selected static's active tier.")
    row = session.scalar(
        select(InventoryItem).where(
            InventoryItem.character_id == character.id,
            InventoryItem.loot_type_id == loot_type.id,
        )
    )
    if quantity == 0 and row is not None:
        session.delete(row)
    elif quantity > 0 and row is None:
        row = InventoryItem(character=character, loot_type=loot_type, quantity=quantity)
        session.add(row)
    elif row is not None:
        row.quantity = quantity
    _audit(session, static.id, actor_id, "LOOT_RESOURCE_SET", "Character", character.id)
    return row if quantity else None


def set_augmentation_material(
    session: Session,
    static: Static,
    character: Character,
    material: AugmentationMaterialType,
    quantity: int,
    actor_id: int,
) -> CharacterAugmentationInventory:
    _nonnegative(quantity, "Quantity")
    _require_character(static, character)
    if static.active_raid_tier_id != material.raid_tier_id:
        raise ValueError("Material is not from the selected static's active tier.")
    row = session.scalar(
        select(CharacterAugmentationInventory).where(
            CharacterAugmentationInventory.character_id == character.id,
            CharacterAugmentationInventory.augmentation_material_type_id == material.id,
        )
    )
    if row is None:
        row = CharacterAugmentationInventory(
            character=character, augmentation_material_type=material, quantity=quantity
        )
        session.add(row)
    else:
        row.quantity = quantity
    _audit(
        session,
        static.id,
        actor_id,
        "AUGMENTATION_INVENTORY_SET",
        "Character",
        character.id,
        str(quantity),
    )
    return row


def set_books(
    session: Session,
    static: Static,
    character: Character,
    floor: RaidFloor,
    earned: int,
    spent: int,
    manual_adjustment: int,
    actor_id: int,
) -> CharacterFloorBookBalance:
    _nonnegative(earned, "Earned books")
    _nonnegative(spent, "Spent books")
    _require_character(static, character)
    if static.active_raid_tier_id != floor.raid_tier_id:
        raise ValueError("Floor is not from the selected static's active tier.")
    row = session.scalar(
        select(CharacterFloorBookBalance).where(
            CharacterFloorBookBalance.character_id == character.id,
            CharacterFloorBookBalance.raid_floor_id == floor.id,
        )
    )
    if row is None:
        row = CharacterFloorBookBalance(character=character, raid_floor=floor)
        session.add(row)
    row.earned = earned
    row.spent = spent
    row.manual_adjustment = manual_adjustment
    _audit(
        session,
        static.id,
        actor_id,
        "BOOKS_SET",
        "Character",
        character.id,
        f"available={row.available}",
    )
    return row


def set_available_books(
    session: Session,
    static: Static,
    character: Character,
    desired_by_floor_id: Mapping[int, int],
    actor_id: int,
    *,
    maximum: int = 1_000_000,
) -> tuple[CharacterFloorBookBalance, ...]:
    """Atomically set effective balances without changing earned or spent books."""
    _require_character(static, character)
    if static.active_raid_tier_id is None:
        raise ValueError("The selected static has no active raid tier.")
    floors = list(
        session.scalars(
            select(RaidFloor)
            .where(RaidFloor.raid_tier_id == static.active_raid_tier_id)
            .order_by(RaidFloor.floor_number)
        )
    )
    if not floors:
        raise ValueError("The active raid tier has no configured floors.")
    if set(desired_by_floor_id) != {floor.id for floor in floors}:
        raise ValueError("Book values must be provided for every configured floor.")
    for value in desired_by_floor_id.values():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("Book balances must be nonnegative whole numbers.")
        if value > maximum:
            raise ValueError(f"Book balances cannot exceed {maximum:,}.")

    existing = {
        row.raid_floor_id: row
        for row in session.scalars(
            select(CharacterFloorBookBalance)
            .where(
                CharacterFloorBookBalance.character_id == character.id,
                CharacterFloorBookBalance.raid_floor_id.in_([floor.id for floor in floors]),
            )
            .with_for_update()
        )
    }
    changes = []
    for floor in floors:
        row = existing.get(floor.id)
        if row is None:
            row = CharacterFloorBookBalance(
                character=character,
                raid_floor=floor,
                earned=0,
                spent=0,
                manual_adjustment=0,
            )
            session.add(row)
        previous_adjustment = row.manual_adjustment
        previous_available = row.available
        desired = desired_by_floor_id[floor.id]
        row.manual_adjustment = desired - row.earned + row.spent
        changes.append((floor, row, previous_adjustment, previous_available, desired))

    session.flush()
    for floor, row, previous_adjustment, previous_available, desired in changes:
        session.add(
            AuditLog(
                static_id=static.id,
                actor_discord_user_id=actor_id,
                action="BOOK_AVAILABLE_ADJUSTED",
                entity_type="CharacterFloorBookBalance",
                entity_id=str(row.id),
                details=json.dumps(
                    {
                        "character_id": character.id,
                        "floor_number": floor.floor_number,
                        "previous_manual_adjustment": previous_adjustment,
                        "new_manual_adjustment": row.manual_adjustment,
                        "previous_effective_balance": previous_available,
                        "new_effective_balance": desired,
                    }
                ),
            )
        )
    return tuple(change[1] for change in changes)


def import_current_state(
    session: Session,
    static: Static,
    source: Mapping[str, Any],
    actor_id: int,
    *,
    dry_run: bool = False,
) -> CurrentStateImportCounts:
    prepared, counts = _validate_current_state(session, static, source)
    if dry_run:
        return counts
    try:
        for character, data in prepared:
            for gear in data.get("gear_slots", []):
                slot = _slot(session, gear["slot"])
                set_gear(
                    session,
                    static,
                    character,
                    slot,
                    GearClassification[gear["current_classification"]],
                    actor_id,
                )
            for inventory in data.get("inventory_items", []):
                if "loot_type" in inventory:
                    loot_type = next(
                        row
                        for row in static.active_raid_tier.loot_types
                        if row.code == inventory["loot_type"]
                    )
                    set_loot_resource(
                        session,
                        static,
                        character,
                        loot_type,
                        inventory["quantity"],
                        actor_id,
                    )
                else:
                    set_inventory(
                        session,
                        static,
                        character,
                        _slot(session, inventory["slot"]),
                        GearClassification[inventory["classification"]],
                        inventory["quantity"],
                        actor_id,
                    )
            tier = static.active_raid_tier
            for books in data.get("books", []):
                assert tier is not None  # Validated before any writes.
                floor = next(f for f in tier.floors if f.floor_number == books["floor"])
                set_books(
                    session,
                    static,
                    character,
                    floor,
                    books.get("earned", 0),
                    books.get("spent", 0),
                    books.get("manual_adjustment", 0),
                    actor_id,
                )
            for material_data in data.get("augmentation_materials", []):
                assert tier is not None  # Validated before any writes.
                material = next(
                    m
                    for m in tier.augmentation_material_types
                    if m.code == material_data["material"]
                )
                set_augmentation_material(
                    session, static, character, material, material_data["quantity"], actor_id
                )
        session.flush()
        return counts
    except Exception:
        session.rollback()
        raise


def slot_status(session: Session, static: Static, character: Character, slot: GearSlot) -> str:
    if static.active_raid_tier_id is None:
        return "No active raid tier"
    result = calculate_character_needs(session, character.id, static.active_raid_tier_id)
    match = next((row for row in result.slot_results if row.slot.id == slot.id), None)
    return match.status.value if match else "UNKNOWN"


def _validate_current_state(session: Session, static: Static, source: Mapping[str, Any]):
    errors: list[str] = []
    rows = source.get("characters")
    if not isinstance(rows, list):
        raise ImportValidationError(["characters: must be an array"])
    prepared = []
    seen_character_slots: set[tuple[int, str]] = set()
    counts = [0, 0, 0, 0, 0]
    for index, data in enumerate(rows):
        context = f"characters[{index}]"
        if not isinstance(data, Mapping):
            errors.append(f"{context}: must be an object")
            continue
        try:
            character = resolve_character(
                session, static, str(data.get("name", "")), str(data.get("world", ""))
            )
        except ValueError as exc:
            errors.append(f"{context}: {exc}")
            continue
        prepared.append((character, data))
        counts[0] += 1
        for gear_index, gear in enumerate(data.get("gear_slots", [])):
            gear_context = f"{context}.gear_slots[{gear_index}]"
            if not isinstance(gear, Mapping):
                errors.append(f"{gear_context}: must be an object")
                continue
            slot_code = gear.get("slot")
            key = (character.id, str(slot_code))
            if key in seen_character_slots:
                errors.append(f"{gear_context}.slot: duplicate character/slot row")
            seen_character_slots.add(key)
            if session.scalar(select(GearSlot.id).where(GearSlot.code == slot_code)) is None:
                errors.append(f"{gear_context}.slot: unknown slot {slot_code}")
            classification = gear.get("current_classification")
            supported = {value.name for value in CURRENT_CLASSIFICATIONS} | {
                GearClassification.NOT_APPLICABLE.name
            }
            if classification not in supported:
                errors.append(
                    f"{gear_context}.current_classification: unknown classification "
                    f"{classification}"
                )
            if slot_code == GearSlotCode.OFFHAND.name:
                expected = (
                    classification != GearClassification.NOT_APPLICABLE.name
                    if not character.job.uses_offhand
                    else classification == GearClassification.NOT_APPLICABLE.name
                )
                if expected:
                    errors.append(
                        f"{gear_context}.current_classification: contradicts configured "
                        "Offhand capability"
                    )
            elif classification == GearClassification.NOT_APPLICABLE.name:
                errors.append(
                    f"{gear_context}.current_classification: NOT_APPLICABLE is Offhand-only"
                )
            for obsolete_field in (
                "current_item",
                "current_item_name",
                "external_item_id",
                "item_level",
                "current_raid_tier",
                "current_raid_tier_id",
                "note",
                "manually_complete",
            ):
                if obsolete_field in gear:
                    errors.append(
                        f"{gear_context}.{obsolete_field}: not accepted for current equipped gear"
                    )
            counts[1] += 1
        for field, count_index in (
            ("inventory_items", 2),
            ("books", 3),
            ("augmentation_materials", 4),
        ):
            values = data.get(field, [])
            if not isinstance(values, list):
                errors.append(f"{context}.{field}: must be an array")
                continue
            if (
                values
                and field in {"books", "augmentation_materials"}
                and static.active_raid_tier is None
            ):
                errors.append(f"{context}.{field}: active raid tier required")
                continue
            counts[count_index] += len(values)
            for value_index, value in enumerate(values):
                value_context = f"{context}.{field}[{value_index}]"
                if not isinstance(value, Mapping):
                    errors.append(f"{value_context}: must be an object")
                    continue
                quantity_fields = ("quantity",) if field != "books" else ("earned", "spent")
                for quantity_field in quantity_fields:
                    quantity = value.get(quantity_field, 0)
                    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
                        errors.append(
                            f"{value_context}.{quantity_field}: must be a nonnegative integer"
                        )
                if field == "inventory_items":
                    has_resource = isinstance(value.get("loot_type"), str)
                    has_category = (
                        value.get("slot") in GearSlotCode.__members__
                        and value.get("classification") in CURRENT_CLASSIFICATIONS
                    )
                    if has_resource == has_category:
                        errors.append(
                            f"{value_context}: define either loot_type or slot/classification"
                        )
                if (
                    field == "books"
                    and static.active_raid_tier is not None
                    and not any(
                        f.floor_number == value.get("floor") for f in static.active_raid_tier.floors
                    )
                ):
                    errors.append(f"{value_context}.floor: unknown floor {value.get('floor')}")
                if (
                    field == "augmentation_materials"
                    and static.active_raid_tier is not None
                    and not any(
                        m.code == value.get("material")
                        for m in static.active_raid_tier.augmentation_material_types
                    )
                ):
                    errors.append(
                        f"{value_context}.material: unknown material {value.get('material')}"
                    )
    if errors:
        raise ImportValidationError(errors)
    return prepared, CurrentStateImportCounts(*counts)


def _gear_row(session: Session, character_id: int, slot_id: int) -> CharacterGearSlot | None:
    return session.scalar(
        select(CharacterGearSlot).where(
            CharacterGearSlot.character_id == character_id,
            CharacterGearSlot.gear_slot_id == slot_id,
        )
    )


def _slot(session: Session, code: str) -> GearSlot:
    row = session.scalar(select(GearSlot).where(GearSlot.code == code))
    if row is None:
        raise ValueError(f"Unknown gear slot: {code}.")
    return row


def _require_character(static: Static, character: Character) -> None:
    if character.static_member.static_id != static.id:
        raise ValueError("Character is not in the selected static.")


def _require_current_classification(value: GearClassification, slot: GearSlotCode) -> None:
    if value not in CURRENT_CLASSIFICATIONS:
        raise ValueError("Current classification must be one of the supported current values.")


def _require_applicable_slot(character: Character, slot: GearSlot) -> None:
    if slot.code is GearSlotCode.OFFHAND and not character.job.uses_offhand:
        raise ValueError("Offhand is N/A for this job.")


def _nonnegative(value: int, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} cannot be negative.")


def _audit(
    session: Session,
    static_id: int,
    actor_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    detail: str | None = None,
) -> None:
    session.add(
        AuditLog(
            static_id=static_id,
            actor_discord_user_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=json.dumps({"detail": detail}) if detail is not None else None,
        )
    )
