"""Immutable proposal values for the pure Regular V2 planner."""

from dataclasses import dataclass, field

from app.models import CharacterKind, ClearMode, GearClassification, GearSlotCode


@dataclass(frozen=True, slots=True)
class RegularScore:
    hierarchy_position: int
    assignments_in_proposal: int
    prior_receipts: int
    prior_material_grants: int
    roster_order: int
    character_id: int

    @property
    def comparison_key(self) -> tuple[int, int, int, int, int]:
        return (
            self.hierarchy_position,
            self.assignments_in_proposal,
            self.prior_receipts + self.prior_material_grants,
            self.roster_order,
            self.character_id,
        )


@dataclass(frozen=True, slots=True)
class ProposedGearEffect:
    slot_key: GearSlotCode
    resulting_category: GearClassification


@dataclass(frozen=True, slots=True)
class RegularAssignment:
    floor_number: int
    loot_type: str
    primary_slot: GearSlotCode | None
    material_type: str | None
    recipient_id: int
    recipient_job: str | None
    recipient_kind: CharacterKind
    hierarchy_position: int | None
    gear_effects: tuple[ProposedGearEffect, ...]
    score: RegularScore
    reason: str


@dataclass(frozen=True, slots=True)
class UnassignedRegularLoot:
    floor_number: int
    loot_type: str
    gear_slot: GearSlotCode | None
    material_type: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class RegularPlanProposal:
    static_id: int
    week_id: int
    week_number: int
    mode: ClearMode
    fingerprint: str
    assignments: tuple[RegularAssignment, ...]
    unassigned: tuple[UnassignedRegularLoot, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    static_name: str | None = None
    week_start: object | None = None
