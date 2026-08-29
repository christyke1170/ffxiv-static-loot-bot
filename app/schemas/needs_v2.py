"""Immutable value results for the tier-neutral needs calculator."""

from dataclasses import dataclass
from enum import StrEnum

from app.models import GearClassification, GearSlotCode


class NeedsV2Status(StrEnum):
    COMPLETE = "COMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MANUALLY_COMPLETE = "MANUALLY_COMPLETE"
    NEEDS_SAVAGE_DROP = "NEEDS_SAVAGE_DROP"
    OWNED_COFFER_AVAILABLE = "OWNED_COFFER_AVAILABLE"
    NEEDS_BASE_TOME = "NEEDS_BASE_TOME"
    NEEDS_AUGMENTATION = "NEEDS_AUGMENTATION"
    READY_TO_AUGMENT = "READY_TO_AUGMENT"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"


@dataclass(frozen=True, slots=True)
class NeedsV2SlotResult:
    character_id: int | None
    static_id: int | None
    job_id: int | None
    job_abbreviation: str | None
    bis_set_id: int | None
    gear_slot_id: int
    gear_slot: GearSlotCode
    slot_name: str
    sort_order: int
    desired: GearClassification | None
    current: GearClassification | None
    status: NeedsV2Status
    required_floor_number: int | None = None
    required_loot_type_code: str | None = None
    required_base_category: GearClassification | None = None
    base_category_owned: bool = False
    required_material_type_id: int | None = None
    material_available: bool = False
    coffer_allocated: bool = False
    explanation: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NeedsV2SavageNeed:
    floor_number: int
    loot_type_code: str
    quantity: int
    slot_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NeedsV2MaterialNeed:
    material_type_id: int
    material_code: str
    material_name: str
    total_required: int
    owned: int
    allocated: int
    additional_needed: int
    slot_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NeedsV2BookBalance:
    floor_number: int
    available: int


@dataclass(frozen=True, slots=True)
class NeedsV2CofferSummary:
    loot_type_code: str
    owned: int
    allocated: int
    slot_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CharacterNeedsV2Result:
    character_id: int | None
    character_name: str | None
    static_id: int | None
    static_name: str | None
    job_id: int | None
    job_abbreviation: str | None
    bis_set_id: int | None
    bis_set_name: str | None
    slot_results: tuple[NeedsV2SlotResult, ...]
    complete_slot_count: int
    applicable_slot_count: int
    full_bis: bool
    savage_needs: tuple[NeedsV2SavageNeed, ...]
    material_needs: tuple[NeedsV2MaterialNeed, ...]
    book_balances: tuple[NeedsV2BookBalance, ...]
    coffer_summaries: tuple[NeedsV2CofferSummary, ...]
    configuration_warnings: tuple[str, ...] = ()


# Public neutral name for callers of the side-by-side calculator.
CharacterNeedsResult = CharacterNeedsV2Result
