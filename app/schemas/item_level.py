"""Frozen, Discord-independent relative item-level results."""

from dataclasses import dataclass
from decimal import Decimal

from app.models import GearClassification, GearSlotCode


@dataclass(frozen=True, slots=True)
class SlotItemLevel:
    slot: GearSlotCode
    display_name: str
    category: GearClassification | None
    calculated_item_level: int | None


@dataclass(frozen=True, slots=True)
class CharacterItemLevelResult:
    character_id: int
    display_name: str
    static_id: int
    crafted_baseline: int | None
    job: str
    uses_offhand: bool
    slots: tuple[SlotItemLevel, ...]
    weapon_contribution: Decimal | None
    exact_average: Decimal | None
    average_item_level: int | None
    is_valid: bool
    garbage_slots: tuple[GearSlotCode, ...]
    missing_or_invalid_slots: tuple[GearSlotCode, ...]
    warnings: tuple[str, ...]
