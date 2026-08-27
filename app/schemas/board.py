"""Presentation-neutral static gear-board view models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.models import GearClassification, GearSlotCode
from app.schemas.needs import NeedStatus


class DisplayStatus(StrEnum):
    BIS = "BIS"
    ALTERNATE = "ALTERNATE"
    TOME_NEEDS_AUGMENT = "TOME_NEEDS_AUGMENT"
    CRAFTED_EX = "CRAFTED_EX"
    NA = "NA"
    NEEDS_REPLACEMENT = "NEEDS_REPLACEMENT"


@dataclass(frozen=True, slots=True)
class BoardBook:
    floor_number: int
    earned: int
    spent: int
    manual_adjustment: int
    available: int
    remaining_required: int


@dataclass(frozen=True, slots=True)
class BoardMaterial:
    code: str
    name: str
    owned: int
    needed: int


@dataclass(frozen=True, slots=True)
class BoardSlot:
    code: GearSlotCode
    name: str
    sort_order: int
    desired_classification: GearClassification | None
    current_classification: GearClassification | None
    needs_status: NeedStatus
    display_status: DisplayStatus
    required_floor_number: int | None
    required_loot_type: str | None
    last_updated: datetime | None
    explanation: str
    required_loot_type_code: str | None = None


@dataclass(frozen=True, slots=True)
class BoardPlayer:
    character_id: int
    display_name: str
    character_name: str
    world: str
    character_kind: str
    job: str | None
    bis_set: str | None
    gear_set_url: str | None
    slots: tuple[BoardSlot, ...]
    books: tuple[BoardBook, ...]
    materials: tuple[BoardMaterial, ...]
    complete_slots: int
    applicable_slots: int
    average_item_level: int | None = None
    item_level_warnings: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StaticGearBoard:
    static_id: int
    static_name: str
    guild_id: int
    tier_id: int
    tier_name: str
    member_discord_user_ids: tuple[int, ...]
    players: tuple[BoardPlayer, ...]
    refreshed_at: datetime
    warnings: tuple[str, ...] = field(default_factory=tuple)
    current_week: int | None = None
