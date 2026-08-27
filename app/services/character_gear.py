"""Shared initialization and Offhand reconciliation for character gear."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Character, CharacterGearSlot, GearClassification, GearSlot, GearSlotCode


def initialize_character_gear(
    session: Session, character: Character
) -> tuple[CharacterGearSlot, ...]:
    """Create every missing slot without requiring a tier or overwriting existing state."""
    session.flush()
    slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
    existing = {row.gear_slot_id: row for row in character.gear_slots}
    created = []
    for slot in slots:
        if slot.id in existing:
            continue
        classification = (
            GearClassification.NOT_APPLICABLE
            if slot.code is GearSlotCode.OFFHAND and not character.job.uses_offhand
            else GearClassification.CRAFTED_EX
        )
        row = CharacterGearSlot(
            character=character, gear_slot=slot, current_classification=classification
        )
        session.add(row)
        created.append(row)
    session.flush()
    return tuple(created)


def reconcile_character_offhand(session: Session, character: Character) -> CharacterGearSlot:
    """Reconcile only Offhand after a job change, preserving meaningful offhand state."""
    offhand = session.scalar(select(GearSlot).where(GearSlot.code == GearSlotCode.OFFHAND))
    if offhand is None:
        raise ValueError("Offhand gear-slot configuration is missing.")
    row = session.scalar(
        select(CharacterGearSlot).where(
            CharacterGearSlot.character_id == character.id,
            CharacterGearSlot.gear_slot_id == offhand.id,
        )
    )
    if row is None:
        row = CharacterGearSlot(character=character, gear_slot=offhand)
        session.add(row)
    if not character.job.uses_offhand:
        row.current_classification = GearClassification.NOT_APPLICABLE
        row.manually_complete = False
    elif (
        row.current_classification is GearClassification.NOT_APPLICABLE
        or row.current_classification is None
    ):
        row.current_classification = GearClassification.CRAFTED_EX
        row.manually_complete = False
    session.flush()
    return row
