"""Typed, non-persisted results produced by the remaining-BiS-needs engine."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.models import (
    AugmentationMaterialType,
    BisSet,
    Character,
    GearClassification,
    GearSlot,
    LootType,
    RaidFloor,
    RaidTier,
)


class NeedStatus(StrEnum):
    """Primary completion or remaining-need state for one BiS slot."""

    COMPLETE = "COMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MANUALLY_COMPLETE = "MANUALLY_COMPLETE"
    NEEDS_SAVAGE_DROP = "NEEDS_SAVAGE_DROP"
    OWNED_COFFER_AVAILABLE = "OWNED_COFFER_AVAILABLE"
    NEEDS_BASE_TOME_ITEM = "NEEDS_BASE_TOME_ITEM"
    NEEDS_AUGMENTATION = "NEEDS_AUGMENTATION"
    READY_TO_AUGMENT = "READY_TO_AUGMENT"
    NEEDS_CATEGORY = "NEEDS_CATEGORY"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"


class BookAvailability(StrEnum):
    """Simulated book-purchase alternative after earlier slots reserve books."""

    NOT_AVAILABLE = "NOT_AVAILABLE"
    PURCHASABLE_WITH_BOOKS = "PURCHASABLE_WITH_BOOKS"
    NEEDS_MORE_BOOKS = "NEEDS_MORE_BOOKS"


@dataclass(slots=True)
class SlotNeedResult:
    character: Character
    bis_set: BisSet
    slot: GearSlot
    desired_classification: GearClassification | None
    current_classification: GearClassification | None
    status: NeedStatus
    required_raid_floor: RaidFloor | None = None
    required_loot_type: LootType | None = None
    base_tome_item_owned: bool = False
    required_augmentation_material: AugmentationMaterialType | None = None
    enough_augmentation_material: bool = False
    book_cost: int | None = None
    effective_books_available: int = 0
    additional_books_needed: int = 0
    book_availability: BookAvailability = BookAvailability.NOT_AVAILABLE
    matching_unopened_coffer_owned: bool = False
    explanation: str = ""
    validation_warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.status in {
            NeedStatus.COMPLETE,
            NeedStatus.MANUALLY_COMPLETE,
            NeedStatus.NOT_APPLICABLE,
        }

    @property
    def is_applicable(self) -> bool:
        return self.status is not NeedStatus.NOT_APPLICABLE


@dataclass(slots=True)
class SavageLootNeed:
    raid_floor: RaidFloor
    loot_type: LootType
    quantity: int
    slots: list[GearSlot]


@dataclass(slots=True)
class AugmentationNeed:
    material: AugmentationMaterialType
    total_units_required: int
    units_owned: int
    units_allocated: int
    additional_units_needed: int
    slots: list[GearSlot]


@dataclass(slots=True)
class MaterialOwnership:
    material: AugmentationMaterialType
    units_owned: int


@dataclass(slots=True)
class BookRequirement:
    raid_floor: RaidFloor
    total_book_cost: int
    effective_books_owned: int
    books_allocated: int
    additional_books_needed: int
    slots: list[GearSlot]


@dataclass(slots=True)
class OwnedCofferAvailability:
    loot_type: LootType
    units_owned: int
    units_allocated: int
    slots: list[GearSlot]


@dataclass(slots=True)
class CharacterNeedsResult:
    character: Character
    raid_tier: RaidTier
    selected_bis_set: BisSet | None
    slot_results: list[SlotNeedResult]
    complete_slot_count: int
    total_applicable_slot_count: int
    is_full_bis: bool
    savage_loot_needs: list[SavageLootNeed]
    augmentation_needs: list[AugmentationNeed]
    materials_owned: list[MaterialOwnership]
    book_requirements: list[BookRequirement]
    owned_unopened_coffers: list[OwnedCofferAvailability]
    configuration_warnings: list[str] = field(default_factory=list)
