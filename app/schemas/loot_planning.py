"""Typed, transport-independent results for read-only Regular loot planning."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.models import CharacterKind, ClearMode, PlannedLootDisposition


class LootPlanningIssueSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class LootPlanningIssueCode(StrEnum):
    STATIC_NOT_FOUND = "STATIC_NOT_FOUND"
    INACTIVE_STATIC = "INACTIVE_STATIC"
    MISSING_ACTIVE_TIER = "MISSING_ACTIVE_TIER"
    INVALID_MEMBER_COUNT = "INVALID_MEMBER_COUNT"
    MISSING_MAIN = "MISSING_MAIN"
    DUPLICATE_MAIN = "DUPLICATE_MAIN"
    INVALID_MAIN_BINDING = "INVALID_MAIN_BINDING"
    UNSUPPORTED_JOB = "UNSUPPORTED_JOB"
    MISSING_BIS = "MISSING_BIS"
    CROSS_TIER_BIS = "CROSS_TIER_BIS"
    INVALID_BIS_CONFIGURATION = "INVALID_BIS_CONFIGURATION"
    MISSING_LOOT_CONFIGURATION = "MISSING_LOOT_CONFIGURATION"
    INACTIVE_MAIN = "INACTIVE_MAIN"
    MISSING_ALT = "MISSING_ALT"
    DUPLICATE_ALT = "DUPLICATE_ALT"
    INACTIVE_ALT = "INACTIVE_ALT"
    INVALID_ALT_BINDING = "INVALID_ALT_BINDING"
    CROSS_STATIC_CHARACTER = "CROSS_STATIC_CHARACTER"
    UNKNOWN_COMBAT_ROLE = "UNKNOWN_COMBAT_ROLE"
    NO_VALID_SPLIT_COMPOSITION = "NO_VALID_SPLIT_COMPOSITION"


class CombatRole(StrEnum):
    TANK = "TANK"
    HEALER = "HEALER"
    DPS = "DPS"


class SplitRejectionCode(StrEnum):
    INVALID_PARTICIPANT_COUNT = "INVALID_PARTICIPANT_COUNT"
    DUPLICATE_CHARACTER = "DUPLICATE_CHARACTER"
    DUPLICATE_MEMBER = "DUPLICATE_MEMBER"
    INVALID_DESIGNATION_COUNT = "INVALID_DESIGNATION_COUNT"
    INVALID_ROLE_COMPOSITION = "INVALID_ROLE_COMPOSITION"
    NON_COMPLEMENTARY_RUNS = "NON_COMPLEMENTARY_RUNS"


@dataclass(frozen=True, slots=True)
class LootPlanningIssue:
    code: LootPlanningIssueCode
    severity: LootPlanningIssueSeverity
    message: str


@dataclass(frozen=True, slots=True)
class PlanningStatic:
    name: str


@dataclass(frozen=True, slots=True)
class PlanningRaidTier:
    name: str


@dataclass(frozen=True, slots=True)
class RegularPlanParticipant:
    roster_order: int
    character_name: str
    world: str
    job: str
    designation: CharacterKind = CharacterKind.MAIN


@dataclass(frozen=True, slots=True)
class MaterialFairnessContext:
    material_label: str
    confirmed_reclear_grants: int
    current_remaining_need: int


@dataclass(frozen=True, slots=True)
class RegularLootAssignment:
    floor_number: int
    floor_name: str
    loot_label: str
    disposition: PlannedLootDisposition
    recipient: RegularPlanParticipant | None
    recipient_job: str | None
    recipient_designation: CharacterKind | None
    explanation: str
    fairness: MaterialFairnessContext | None = None


@dataclass(frozen=True, slots=True)
class RegularLootPlanRun:
    name: str
    participants: tuple[RegularPlanParticipant, ...]
    assignments: tuple[RegularLootAssignment, ...]


@dataclass(frozen=True, slots=True)
class RegularLootPlanResult:
    static: PlanningStatic | None
    active_tier: PlanningRaidTier | None
    target_week: int | None
    mode: ClearMode = ClearMode.REGULAR
    is_valid: bool = False
    issues: tuple[LootPlanningIssue, ...] = field(default_factory=tuple)
    run: RegularLootPlanRun | None = None

    @property
    def errors(self) -> tuple[LootPlanningIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is LootPlanningIssueSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[LootPlanningIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is LootPlanningIssueSeverity.WARNING
        )


@dataclass(frozen=True, slots=True)
class SplitRoleCounts:
    tanks: int
    healers: int
    dps: int


@dataclass(frozen=True, slots=True)
class SplitRosterParticipant:
    roster_order: int
    static_member_id: int
    static_member_name: str
    character_id: int
    character_name: str
    world: str
    job: str
    job_name: str
    combat_role: CombatRole
    designation: CharacterKind


@dataclass(frozen=True, slots=True)
class SplitRosterRun:
    name: str
    participants: tuple[SplitRosterParticipant, ...]
    role_counts: SplitRoleCounts


@dataclass(frozen=True, slots=True)
class SplitCandidateRejection:
    partition_ordinal: int
    candidate_identifier: str
    run_name: str | None
    code: SplitRejectionCode
    message: str
    role_counts: SplitRoleCounts | None = None


@dataclass(frozen=True, slots=True)
class SplitRosterCandidate:
    partition_ordinal: int
    candidate_identifier: str
    run_a: SplitRosterRun
    run_b: SplitRosterRun


@dataclass(frozen=True, slots=True)
class SplitRosterCandidatesResult:
    static: PlanningStatic | None
    active_tier: PlanningRaidTier | None
    target_week: int | None
    mode: ClearMode = ClearMode.SPLIT
    is_valid: bool = False
    issues: tuple[LootPlanningIssue, ...] = field(default_factory=tuple)
    total_partitions_evaluated: int = 0
    total_candidates_rejected: int = 0
    candidates: tuple[SplitRosterCandidate, ...] = field(default_factory=tuple)
    rejections: tuple[SplitCandidateRejection, ...] = field(default_factory=tuple)

    @property
    def total_valid_candidates(self) -> int:
        return len(self.candidates)

    @property
    def errors(self) -> tuple[LootPlanningIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is LootPlanningIssueSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[LootPlanningIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is LootPlanningIssueSeverity.WARNING
        )


@dataclass(frozen=True, slots=True)
class SplitSavageAssignment:
    candidate_ordinal: int
    run_name: str
    floor_number: int
    floor_name: str
    loot_label: str
    disposition: PlannedLootDisposition
    recipient: SplitRosterParticipant | None
    recipient_job: str | None
    recipient_designation: CharacterKind | None
    explanation: str


@dataclass(frozen=True, slots=True)
class SplitSavageRunPlan:
    name: str
    assignments: tuple[SplitSavageAssignment, ...]


@dataclass(frozen=True, slots=True)
class SplitMaterialAssignment:
    candidate_ordinal: int
    run_name: str
    floor_number: int
    floor_name: str
    material_label: str
    disposition: PlannedLootDisposition
    recipient: SplitRosterParticipant | None
    recipient_job: str | None
    recipient_designation: CharacterKind | None
    confirmed_grant_count: int
    remaining_need: int
    explanation: str


@dataclass(frozen=True, slots=True)
class SplitWeaponUpgradeAssignment:
    candidate_ordinal: int
    run_name: str
    recipient: SplitRosterParticipant | None
    recipient_job: str | None
    recipient_designation: CharacterKind | None
    current_weapon_classification: str | None
    tomestone_floor_number: int
    tomestone_floor_name: str
    augment_floor_number: int
    augment_floor_name: str
    disposition: PlannedLootDisposition
    explanation: str


@dataclass(frozen=True, slots=True)
class SplitCarrySignature:
    """Carry comparison, highest DPS priority first: separated carries score 1, else 0."""

    separated_completed_dps: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SplitSavagePlanCandidate:
    candidate_ordinal: int
    candidate_identifier: str
    run_a: SplitSavageRunPlan
    run_b: SplitSavageRunPlan
    main_assignment_vector: tuple[int, ...]
    carry_balance_signature: SplitCarrySignature
    total_useful_alt_assignments: int
    alt_assignment_vector: tuple[int, ...]
    comparison_key: tuple[object, ...]
    avoided_main_conflicts: tuple[str, ...] = field(default_factory=tuple)
    twine_assignments: tuple[SplitMaterialAssignment, ...] = field(default_factory=tuple)
    glaze_assignments: tuple[SplitMaterialAssignment, ...] = field(default_factory=tuple)
    weapon_upgrades: tuple[SplitWeaponUpgradeAssignment, ...] = field(default_factory=tuple)
    twine_score: tuple[int, ...] = field(default_factory=tuple)
    glaze_score: tuple[int, ...] = field(default_factory=tuple)
    useful_paired_weapon_upgrades: int = 0


@dataclass(frozen=True, slots=True)
class SplitSavageRunnerUp:
    candidate_ordinal: int
    candidate_identifier: str
    main_assignment_vector: tuple[int, ...]
    carry_balance_signature: SplitCarrySignature
    total_useful_alt_assignments: int
    alt_assignment_vector: tuple[int, ...]
    twine_score: tuple[int, ...] = field(default_factory=tuple)
    glaze_score: tuple[int, ...] = field(default_factory=tuple)
    useful_paired_weapon_upgrades: int = 0


@dataclass(frozen=True, slots=True)
class SplitSavagePlanResult:
    static: PlanningStatic | None
    active_tier: PlanningRaidTier | None
    target_week: int | None
    is_valid: bool
    issues: tuple[LootPlanningIssue, ...] = field(default_factory=tuple)
    valid_candidates_evaluated: int = 0
    winner: SplitSavagePlanCandidate | None = None
    runner_up: SplitSavageRunnerUp | None = None
    selection_reasoning: str = ""

    @property
    def errors(self) -> tuple[LootPlanningIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is LootPlanningIssueSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[LootPlanningIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is LootPlanningIssueSeverity.WARNING
        )
