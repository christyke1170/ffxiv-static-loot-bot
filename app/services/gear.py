"""Neutral current gear and resource operations."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import (
    AuditLog,
    Character,
    CharacterGearSlot,
    GearClassification,
    GearSlot,
    GearSlotCode,
)
from app.services.neutral_resources import set_current_balance, validate_resource_key

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


def resolve_character(session, static, name, world=None):
    q = select(Character).where(
        Character.name == name, Character.static_member.has(static_id=static.id)
    )
    q = q.where(Character.world == world) if world else q
    rows = list(session.scalars(q))
    if not rows:
        raise ValueError("Character is not in the selected static.")
    if len(rows) > 1:
        raise ValueError("Character name is ambiguous; include the world.")
    return rows[0]


def set_gear(session, static, character, slot, classification, actor_id):
    _require_character(static, character)
    if classification is GearClassification.NOT_APPLICABLE:
        if slot.code is not GearSlotCode.OFFHAND or character.job.uses_offhand:
            raise ValueError("NOT_APPLICABLE is valid only for a configured non-offhand job.")
    elif slot.code is GearSlotCode.OFFHAND and not character.job.uses_offhand:
        raise ValueError("Offhand is N/A for this job.")
    elif classification not in CURRENT_CLASSIFICATIONS:
        raise ValueError("Unsupported current classification.")
    row = _gear_row(session, character.id, slot.id)
    if row is None:
        row = CharacterGearSlot(
            character=character, gear_slot=slot, current_classification=classification
        )
        session.add(row)
    row.current_classification = classification
    row.manually_complete = False
    row.updated_at = datetime.now(UTC)
    session.flush()
    _audit(
        session, static.id, actor_id, "GEAR_SET", "CharacterGearSlot", row.id, classification.value
    )
    return row


def clear_gear(session, static, character, slot, actor_id):
    _require_character(static, character)
    row = _gear_row(session, character.id, slot.id)
    if row is None:
        raise ValueError("That gear slot is already empty.")
    session.delete(row)
    _audit(session, static.id, actor_id, "GEAR_CLEARED", "CharacterGearSlot", row.id)


def set_manual_completion(session, static, character, slot, complete, actor_id, reason=None):
    _require_character(static, character)
    row = _gear_row(session, character.id, slot.id)
    if row is None:
        raise ValueError("Set current gear before marking completion.")
    row.manually_complete = bool(complete)
    _audit(session, static.id, actor_id, "GEAR_COMPLETION_SET", "CharacterGearSlot", row.id, reason)
    return row


def set_inventory(session, static, character, slot, classification, quantity, actor_id):
    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")
    return set_current_balance(
        session, static, character, f"{classification.value}_{slot.code.value}", quantity
    )


def import_current_state(session, static, source: Mapping, actor_id, *, dry_run=False):
    rows = source.get("characters", [])
    counts = CurrentStateImportCounts(characters=len(rows))
    if not isinstance(rows, list):
        raise ValueError("characters: must be an array")
    if dry_run:
        return counts
    gears = items = books = mats = 0
    for data in rows:
        c = resolve_character(
            session, static, str(data.get("name", "")), str(data.get("world", ""))
        )
        for g in data.get("gear_slots", []):
            set_gear(
                session,
                static,
                c,
                _slot(session, g["slot"]),
                GearClassification[g["current_classification"]],
                actor_id,
            )
            gears += 1
        for item in data.get("inventory_items", []):
            key = validate_resource_key(item.get("loot_type", f"BOOK_FLOOR_{item.get('floor', 1)}"))
            set_current_balance(session, static, c, key, item.get("quantity", 0))
            items += 1
        for item in data.get("augmentation_materials", []):
            set_current_balance(
                session, static, c, validate_resource_key(item["material"]), item.get("quantity", 0)
            )
            mats += 1
        for item in data.get("books", []):
            set_current_balance(
                session,
                static,
                c,
                validate_resource_key(f"BOOK_FLOOR_{item['floor']}"),
                item.get("earned", 0) - item.get("spent", 0),
            )
            books += 1
    return CurrentStateImportCounts(len(rows), gears, items, books, mats)


def slot_status(session, static, character, slot):
    return "UNKNOWN"


def _gear_row(session, cid, sid):
    return session.scalar(
        select(CharacterGearSlot).where(
            CharacterGearSlot.character_id == cid, CharacterGearSlot.gear_slot_id == sid
        )
    )


def _slot(session, code):
    row = session.scalar(select(GearSlot).where(GearSlot.code == code.strip().upper()))
    if row is None:
        raise ValueError(f"Unknown gear slot: {code}.")
    return row


def _require_character(static, character):
    if character.static_member.static_id != static.id:
        raise ValueError("Character is not in the selected static.")


def _audit(session, static_id, actor, action, entity, entity_id, details=None):
    session.add(
        AuditLog(
            static_id=static_id,
            actor_discord_user_id=actor,
            action=action,
            entity_type=entity,
            entity_id=str(entity_id),
            details=details,
        )
    )
