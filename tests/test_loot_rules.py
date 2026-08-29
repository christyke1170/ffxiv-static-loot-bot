"""Focused tests for the immutable application loot rules."""

from dataclasses import FrozenInstanceError

import pytest

from app.domain import loot_rules
from app.models import GearSlotCode


def test_exactly_four_ordered_floors():
    assert loot_rules.all_floors() == (1, 2, 3, 4)
    assert loot_rules.ALL_FLOORS == (1, 2, 3, 4)


@pytest.mark.parametrize(
    "slot",
    [
        GearSlotCode.EARRINGS,
        GearSlotCode.NECKLACE,
        GearSlotCode.BRACELETS,
        GearSlotCode.RING_1,
        GearSlotCode.RING_2,
    ],
)
def test_accessories_are_floor_one(slot):
    assert loot_rules.floor_for_savage(slot) == 1
    assert loot_rules.savage_loot_type(slot) == "ACCESSORY_COFFER"


@pytest.mark.parametrize(
    ("slot", "loot"),
    [
        (GearSlotCode.HEAD, "HEAD_COFFER"),
        (GearSlotCode.HANDS, "GLOVES_COFFER"),
        (GearSlotCode.FEET, "BOOTS_COFFER"),
    ],
)
def test_floor_two_armor_slots(slot, loot):
    assert loot_rules.floor_for_savage(slot) == 2
    assert loot_rules.savage_loot_type(slot) == loot


@pytest.mark.parametrize("slot", [GearSlotCode.BODY, GearSlotCode.LEGS])
def test_floor_three_body_slots(slot):
    assert loot_rules.floor_for_savage(slot) == 3


def test_floor_four_weapon_and_applicable_offhand():
    assert loot_rules.floor_for_savage(GearSlotCode.WEAPON) == 4
    assert loot_rules.floor_for_savage(GearSlotCode.OFFHAND) == 4
    assert loot_rules.savage_loot_type(GearSlotCode.OFFHAND) == "WEAPON_COFFER"


def test_augmentation_material_floors():
    assert loot_rules.floor_for_material("ACCESSORY_GLAZE") == 2
    assert loot_rules.floor_for_material("ARMOR_TWINE") == 3
    assert loot_rules.augmentation_material_type(GearSlotCode.EARRINGS) == "ACCESSORY_GLAZE"
    assert loot_rules.supports(GearSlotCode.OFFHAND)


def test_rules_are_immutable_and_have_no_database_or_item_ids():
    rule = loot_rules.savage_rule(GearSlotCode.WEAPON)
    assert rule is not None
    assert not hasattr(rule, "id")
    with pytest.raises(FrozenInstanceError):
        rule.floor = 1
    assert loot_rules.supports(GearSlotCode.WEAPON)
    assert loot_rules.supports(GearSlotCode.OFFHAND)
