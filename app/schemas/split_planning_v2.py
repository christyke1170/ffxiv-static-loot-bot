"""Immutable, tier-neutral proposal values for pure Split V2 planning."""

from dataclasses import dataclass, field

from app.models import CharacterKind, ClearMode, GearSlotCode
from app.schemas.regular_planning_v2 import ProposedGearEffect


@dataclass(frozen=True, slots=True)
class SplitScore:
    designation_priority: int
    hierarchy_position: int
    assignments_in_proposal: int
    fairness_count: int
    member_id: int
    character_id: int
    remaining_need: int = 0
    combined_fairness_count: int = 0

    @property
    def comparison_key(self) -> tuple[int, int, int, int, int, int, int]:
        return (
            self.designation_priority,
            self.combined_fairness_count,
            -self.remaining_need,
            self.hierarchy_position,
            self.fairness_count,
            self.member_id,
            self.character_id,
        )


@dataclass(frozen=True, slots=True)
class SplitPartitionScore:
    """Complete two-run score, compared lexicographically from first field to last."""

    main_savage_vector: tuple[int, ...]
    material_quality: tuple[int, ...]
    completed_dps_separation: tuple[int, ...]
    useful_alt_savage_count: int
    alt_savage_vector: tuple[int, ...]
    useful_tome_weapon_upgrades: int
    canonical_partition_order: int

    @property
    def comparison_key(self) -> tuple[object, ...]:
        return (
            self.main_savage_vector,
            self.material_quality,
            self.completed_dps_separation,
            self.useful_alt_savage_count,
            self.alt_savage_vector,
            self.useful_tome_weapon_upgrades,
            -self.canonical_partition_order,
        )


@dataclass(frozen=True, slots=True)
class SplitAssignment:
    group_id: int
    group_number: int
    floor_number: int
    loot_key: str
    primary_slot: GearSlotCode | None
    material_key: str | None
    recipient_id: int | None
    recipient_job: str | None
    recipient_kind: CharacterKind | None
    owned_alt_id: int | None
    hierarchy_position: int | None
    assignments_in_proposal: int
    fairness_count: int
    gear_effects: tuple[ProposedGearEffect, ...]
    resource_quantity: int
    score: SplitScore | None
    reason: str


@dataclass(frozen=True, slots=True)
class SplitGroupProposal:
    group_id: int
    group_number: int
    participant_ids: tuple[int, ...]
    assignments: tuple[SplitAssignment, ...]


@dataclass(frozen=True, slots=True)
class UnassignedSplitLoot:
    group_id: int
    group_number: int
    floor_number: int
    loot_key: str
    primary_slot: GearSlotCode | None
    material_key: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class SplitPlanProposal:
    static_id: int
    week_id: int
    week_number: int
    mode: ClearMode
    fingerprint: str
    groups: tuple[SplitGroupProposal, ...]
    unassigned: tuple[UnassignedSplitLoot, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    score: SplitPartitionScore | None = None
    partitions_evaluated: int = 0
    static_name: str | None = None
    week_start: object | None = None


class SplitPlanningV2Error(ValueError):
    """The immutable Split state cannot safely produce a proposal."""
