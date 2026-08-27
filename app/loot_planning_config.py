"""Stable configuration for read-only Regular loot planning."""

from dataclasses import dataclass

from app.models import GearSlotCode, LootCategory

REGULAR_JOB_PRIORITY: tuple[str, ...] = (
    "SAM",
    "VPR",
    "BLM",
    "RPR",
    "MNK",
    "DRG",
    "NIN",
    "PCT",
    "SMN",
    "RDM",
    "MCH",
    "DNC",
    "BRD",
    "WHM",
    "SGE",
    "AST",
    "SCH",
    "DRK",
    "GNB",
    "PLD",
    "WAR",
)

_JOB_RANKS = {job: rank for rank, job in enumerate(REGULAR_JOB_PRIORITY, 1)}
UNKNOWN_JOB_PRIORITY = len(REGULAR_JOB_PRIORITY) + 1


def regular_job_priority_rank(job_abbreviation: str) -> int:
    """Return a stable one-based rank, sorting unsupported jobs after supported jobs."""
    return _JOB_RANKS.get(job_abbreviation.strip().upper(), UNKNOWN_JOB_PRIORITY)


def is_supported_regular_job(job_abbreviation: str) -> bool:
    return job_abbreviation.strip().upper() in _JOB_RANKS


def is_supported_combat_job(job_abbreviation: str) -> bool:
    """Return whether the job is part of the authoritative supported combat roster."""
    return is_supported_regular_job(job_abbreviation)


@dataclass(frozen=True, slots=True)
class RegularTrackedDrop:
    floor_number: int
    loot_type_code: str
    label: str
    category: LootCategory
    slot: GearSlotCode | None = None
    material_code: str | None = None


REGULAR_TRACKED_DROPS: tuple[RegularTrackedDrop, ...] = (
    RegularTrackedDrop(
        1, "EARRING_COFFER", "Earring Coffer", LootCategory.COFFER, GearSlotCode.EARRINGS
    ),
    RegularTrackedDrop(
        1, "NECKLACE_COFFER", "Necklace Coffer", LootCategory.COFFER, GearSlotCode.NECKLACE
    ),
    RegularTrackedDrop(
        1, "BRACELET_COFFER", "Bracelet Coffer", LootCategory.COFFER, GearSlotCode.BRACELETS
    ),
    RegularTrackedDrop(1, "RING_COFFER", "Ring Coffer", LootCategory.COFFER, GearSlotCode.RING_1),
    RegularTrackedDrop(2, "HEAD_COFFER", "Head Coffer", LootCategory.COFFER, GearSlotCode.HEAD),
    RegularTrackedDrop(
        2, "GLOVES_COFFER", "Gloves Coffer", LootCategory.COFFER, GearSlotCode.HANDS
    ),
    RegularTrackedDrop(2, "BOOTS_COFFER", "Boots Coffer", LootCategory.COFFER, GearSlotCode.FEET),
    RegularTrackedDrop(
        2,
        "ACCESSORY_GLAZE",
        "Glaze",
        LootCategory.AUGMENTATION_MATERIAL,
        material_code="ACCESSORY_GLAZE",
    ),
    RegularTrackedDrop(3, "CHEST_COFFER", "Chest Coffer", LootCategory.COFFER, GearSlotCode.BODY),
    RegularTrackedDrop(3, "PANTS_COFFER", "Pants Coffer", LootCategory.COFFER, GearSlotCode.LEGS),
    RegularTrackedDrop(
        3,
        "ARMOR_TWINE",
        "Twine",
        LootCategory.AUGMENTATION_MATERIAL,
        material_code="ARMOR_TWINE",
    ),
    RegularTrackedDrop(
        4, "WEAPON_COFFER", "Weapon Coffer", LootCategory.COFFER, GearSlotCode.WEAPON
    ),
)

SPLIT_WEAPON_TOMESTONE_FLOOR = 2
SPLIT_WEAPON_AUGMENT_FLOOR = 3
