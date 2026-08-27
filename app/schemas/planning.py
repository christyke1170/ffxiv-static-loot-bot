"""Typed, Discord-independent weekly roster and loot-plan results."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.models import (
    BisSetItem,
    Character,
    GearSlot,
    Item,
    LootAssignment,
    LootAssignmentState,
    LootPlan,
    LootType,
    RaidFloor,
    ReclearGroup,
    ReclearWeek,
)


class ValidationIssueCode(StrEnum):
    GROUP_COUNT = "GROUP_COUNT"
    GROUP_SIZE = "GROUP_SIZE"
    MAIN_COUNT = "MAIN_COUNT"
    ALT_COUNT = "ALT_COUNT"
    INACTIVE_CHARACTER = "INACTIVE_CHARACTER"
    FOREIGN_MEMBER = "FOREIGN_MEMBER"
    MISSING_MEMBER = "MISSING_MEMBER"
    DUPLICATE_MEMBER = "DUPLICATE_MEMBER"
    DUPLICATE_CHARACTER = "DUPLICATE_CHARACTER"
    MEMBER_NOT_IN_BOTH_GROUPS = "MEMBER_NOT_IN_BOTH_GROUPS"
    MAIN_ALT_NOT_OPPOSITE = "MAIN_ALT_NOT_OPPOSITE"
    MISSING_MAIN = "MISSING_MAIN"
    MISSING_ALT = "MISSING_ALT"
    FLOOR_LOCKOUT = "FLOOR_LOCKOUT"
    INVALID_FLOOR = "INVALID_FLOOR"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: ValidationIssueCode
    message: str
    group_id: int | None = None
    character_id: int | None = None
    raid_floor_id: int | None = None
    blocking: bool = True


@dataclass(slots=True)
class RosterValidationResult:
    reclear_week: ReclearWeek
    requested_floors: list[RaidFloor]
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not any(issue.blocking for issue in self.issues)


@dataclass(frozen=True, slots=True)
class PlanWarning:
    code: str
    message: str
    character_id: int | None = None
    job_id: int | None = None


@dataclass(frozen=True, slots=True)
class RankedEligibleRecipient:
    character: Character
    hierarchy_position: int
    assignments_in_plan: int
    confirmed_priority_receipts: int
    intended_bis_set_item: BisSetItem | None
    intended_slot: GearSlot | None
    intended_final_item: Item | None
    owns_required_base_tome_item: bool | None
    reason: str


@dataclass(frozen=True, slots=True)
class PlannedDropResult:
    assignment: LootAssignment
    reclear_week: ReclearWeek
    group: ReclearGroup
    floor: RaidFloor
    loot_type: LootType
    drop_instance_number: int
    suggested_recipient: Character | None
    intended_recipient: Character | None
    intended_bis_slot: GearSlot | None
    intended_final_item: Item | None
    backup_recipient: Character | None
    state: LootAssignmentState
    reason: str
    recipient_owns_required_base_tome_item: bool | None
    hierarchy_position: int | None


@dataclass(slots=True)
class WeeklyPlanResult:
    reclear_week: ReclearWeek
    plan: LootPlan | None
    validation: RosterValidationResult
    assignments: list[PlannedDropResult] = field(default_factory=list)
    warnings: list[PlanWarning] = field(default_factory=list)
    reused_existing_plan: bool = False


class LootPlanGenerationError(ValueError):
    """Raised when a weekly plan cannot safely be generated."""

    def __init__(self, message: str, validation: RosterValidationResult | None = None) -> None:
        super().__init__(message)
        self.validation = validation
