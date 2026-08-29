"""Immutable, tier-neutral inputs for future weekly planners."""

from dataclasses import dataclass
from datetime import date

from app.models import CharacterKind, ClearMode, ReclearWorkflowState
from app.schemas.needs_v2 import CharacterNeedsResult


@dataclass(frozen=True, slots=True)
class PlanningCharacter:
    character_id: int
    member_id: int
    name: str
    world: str
    kind: CharacterKind
    job_id: int | None
    job_abbreviation: str | None
    uses_offhand: bool
    combat_role: str | None
    hierarchy_position: int | None
    needs: CharacterNeedsResult


@dataclass(frozen=True, slots=True)
class PlanningGroup:
    group_id: int
    group_number: int
    character_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PlanningFloor:
    floor_number: int
    cleared: bool
    eligible_character_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PlanningLockout:
    character_id: int
    floor_number: int
    cleared: bool
    loot_eligible: bool


@dataclass(frozen=True, slots=True)
class PlanningPlan:
    plan_id: int
    status: str
    mode: ClearMode


@dataclass(frozen=True, slots=True)
class PlanningFairness:
    character_id: int
    savage_receipts: int
    material_grants: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class PlanningState:
    static_id: int
    static_name: str
    week_id: int
    week_number: int
    week_start: date
    week_status: ReclearWorkflowState
    mode: ClearMode
    reset_period: date
    mains: tuple[PlanningCharacter, ...]
    alts: tuple[PlanningCharacter, ...]
    ownership: tuple[tuple[int, int], ...]
    groups: tuple[PlanningGroup, ...]
    floors: tuple[PlanningFloor, ...]
    lockouts: tuple[PlanningLockout, ...]
    hierarchy: tuple[tuple[int, str, int], ...]
    active_plan: PlanningPlan | None
    fairness: tuple[PlanningFairness, ...]
    warnings: tuple[str, ...] = ()
