"""Fixed FFXIV loot rules used by the V2 needs and gearboard paths.

These values are application rules, not administrator configuration.  Legacy
database codes are used only as stable translation keys at the persistence
boundary; no database row or equipment item is part of this definition.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import NamedTuple

from app.models import GearClassification, GearSlotCode, LootCategory


@dataclass(frozen=True, slots=True)
class SavageRule:
    floor: int
    loot_type: str


@dataclass(frozen=True, slots=True)
class AugmentationRule:
    floor: int
    material_type: str


class RegularDrop(NamedTuple):
    floor: int
    loot_type: str
    slot: GearSlotCode | None = None
    material_type: str | None = None


_SAVAGE = MappingProxyType(
    {
        GearSlotCode.EARRINGS: SavageRule(1, "ACCESSORY_COFFER"),
        GearSlotCode.NECKLACE: SavageRule(1, "ACCESSORY_COFFER"),
        GearSlotCode.BRACELETS: SavageRule(1, "ACCESSORY_COFFER"),
        GearSlotCode.RING_1: SavageRule(1, "ACCESSORY_COFFER"),
        GearSlotCode.RING_2: SavageRule(1, "ACCESSORY_COFFER"),
        GearSlotCode.HEAD: SavageRule(2, "HEAD_COFFER"),
        GearSlotCode.HANDS: SavageRule(2, "GLOVES_COFFER"),
        GearSlotCode.FEET: SavageRule(2, "BOOTS_COFFER"),
        GearSlotCode.BODY: SavageRule(3, "CHEST_COFFER"),
        GearSlotCode.LEGS: SavageRule(3, "PANTS_COFFER"),
        GearSlotCode.WEAPON: SavageRule(4, "WEAPON_COFFER"),
        GearSlotCode.OFFHAND: SavageRule(4, "WEAPON_COFFER"),
    }
)

_AUGMENTATION = MappingProxyType(
    {
        GearSlotCode.EARRINGS: AugmentationRule(2, "ACCESSORY_GLAZE"),
        GearSlotCode.NECKLACE: AugmentationRule(2, "ACCESSORY_GLAZE"),
        GearSlotCode.BRACELETS: AugmentationRule(2, "ACCESSORY_GLAZE"),
        GearSlotCode.RING_1: AugmentationRule(2, "ACCESSORY_GLAZE"),
        GearSlotCode.RING_2: AugmentationRule(2, "ACCESSORY_GLAZE"),
        GearSlotCode.HEAD: AugmentationRule(3, "ARMOR_TWINE"),
        GearSlotCode.BODY: AugmentationRule(3, "ARMOR_TWINE"),
        GearSlotCode.HANDS: AugmentationRule(3, "ARMOR_TWINE"),
        GearSlotCode.LEGS: AugmentationRule(3, "ARMOR_TWINE"),
        GearSlotCode.FEET: AugmentationRule(3, "ARMOR_TWINE"),
    }
)

_FLOORS = (1, 2, 3, 4)
ALL_FLOORS = _FLOORS


def floors() -> tuple[int, ...]:
    return _FLOORS


all_floors = floors


def savage_rule(slot: GearSlotCode) -> SavageRule | None:
    return _SAVAGE.get(slot)


def floor_for_savage(slot: GearSlotCode) -> int | None:
    rule = savage_rule(slot)
    return rule.floor if rule else None


def savage_loot_type(slot: GearSlotCode) -> str | None:
    rule = savage_rule(slot)
    return rule.loot_type if rule else None


def augmentation_rule(slot: GearSlotCode) -> AugmentationRule | None:
    return _AUGMENTATION.get(slot)


def augmentation_material_type(slot: GearSlotCode) -> str | None:
    rule = augmentation_rule(slot)
    return rule.material_type if rule else None


def floor_for_material(material_type: str) -> int | None:
    return {"ACCESSORY_GLAZE": 2, "ARMOR_TWINE": 3}.get(material_type.strip().upper())


floor_for_augmentation_material = floor_for_material


def supports(slot: GearSlotCode) -> bool:
    return slot in _SAVAGE or slot in _AUGMENTATION


is_supported = supports


def loot_category(code: str) -> LootCategory:
    return (
        LootCategory.AUGMENTATION_MATERIAL
        if code in {"ACCESSORY_GLAZE", "ARMOR_TWINE"}
        else LootCategory.COFFER
    )


def required_rule(slot: GearSlotCode, classification: GearClassification):
    if classification is GearClassification.SAVAGE:
        return savage_rule(slot)
    if classification is GearClassification.AUGMENTED_TOME:
        return augmentation_rule(slot)
    return None


REGULAR_DROPS = (
    RegularDrop(1, "ACCESSORY_COFFER", GearSlotCode.EARRINGS),
    RegularDrop(1, "ACCESSORY_COFFER", GearSlotCode.NECKLACE),
    RegularDrop(1, "ACCESSORY_COFFER", GearSlotCode.BRACELETS),
    RegularDrop(1, "ACCESSORY_COFFER", GearSlotCode.RING_1),
    RegularDrop(2, "HEAD_COFFER", GearSlotCode.HEAD),
    RegularDrop(2, "GLOVES_COFFER", GearSlotCode.HANDS),
    RegularDrop(2, "BOOTS_COFFER", GearSlotCode.FEET),
    RegularDrop(2, "ACCESSORY_GLAZE", material_type="ACCESSORY_GLAZE"),
    RegularDrop(3, "CHEST_COFFER", GearSlotCode.BODY),
    RegularDrop(3, "PANTS_COFFER", GearSlotCode.LEGS),
    RegularDrop(3, "ARMOR_TWINE", material_type="ARMOR_TWINE"),
    RegularDrop(4, "WEAPON_COFFER", GearSlotCode.WEAPON),
)


floor_for_gear_slot = floor_for_savage
savage_loot_for_slot = savage_loot_type
augmentation_material_for_slot = augmentation_material_type
