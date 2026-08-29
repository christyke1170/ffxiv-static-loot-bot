"""Neutral resource/gear visibility through the V2 needs boundary."""

from sqlalchemy import select

from app.models import (
    BisSet,
    BisSetItem,
    CharacterGearSlot,
    GearClassification,
    GearSlot,
    GearSlotCode,
)
from app.services.needs_v2 import calculate_character_needs_v2
from app.services.neutral_resources import set_current_balance
from tests.test_v2_planning_state import _static


def _bis(session, static, character, classification=GearClassification.SAVAGE):
    bis = BisSet(static=static, job=character.job, name=f"BiS {character.name}")
    session.add(bis)
    session.flush()
    head = session.scalar(select(GearSlot).where(GearSlot.code == GearSlotCode.HEAD))
    session.add(BisSetItem(bis_set=bis, gear_slot=head, classification=classification))
    session.flush()
    return head


def test_confirmed_coffer_appears_in_needs_coffer_summary(session):
    static = _static(session)
    character = static.members[0].characters[0]
    _bis(session, static, character)
    set_current_balance(session, static, character, "HEAD_COFFER", 1)
    session.commit()
    result = calculate_character_needs_v2(session, character.id)
    assert result.coffer_summaries[0].owned == 1
    assert result.slot_results[2].coffer_allocated is True


def test_applied_gear_category_is_visible_to_needs(session):
    static = _static(session)
    character = static.members[0].characters[0]
    head = _bis(session, static, character)
    session.add(
        CharacterGearSlot(
            character_id=character.id,
            gear_slot_id=head.id,
            current_classification=GearClassification.SAVAGE,
        )
    )
    session.commit()
    result = calculate_character_needs_v2(session, character.id)
    row = next(row for row in result.slot_results if row.gear_slot is GearSlotCode.HEAD)
    assert row.status.value in {"COMPLETE", "OWNED_COFFER_AVAILABLE"}


def test_material_balance_is_visible_without_completing_augmented_slot(session):
    static = _static(session)
    character = static.members[0].characters[0]
    _bis(session, static, character, GearClassification.AUGMENTED_TOME)
    set_current_balance(session, static, character, "ARMOR_TWINE", 1)
    session.commit()
    result = calculate_character_needs_v2(session, character.id)
    assert result.material_needs
    assert result.material_needs[0].owned == 1
    assert all(row.status.value != "COMPLETE" for row in result.slot_results)


def test_needs_read_is_immutable_and_orm_free(session):
    static = _static(session)
    character = static.members[0].characters[0]
    _bis(session, static, character)
    session.commit()
    result = calculate_character_needs_v2(session, character.id)
    assert isinstance(result.slot_results, tuple)
    assert not any(hasattr(row, "__table__") for row in result.slot_results)
