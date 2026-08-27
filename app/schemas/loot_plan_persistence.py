"""Discord-independent snapshots returned by weekly loot-plan persistence."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.models import CharacterKind, ClearMode, PlannedLootDisposition, WeeklyLootPlanStatus


class LootPlanStalenessState(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNVERIFIABLE = "UNVERIFIABLE"


class LootPlanStaleReasonCode(StrEnum):
    SOURCE_STATE_CHANGED = "SOURCE_STATE_CHANGED"
    COMPLETED_WEEK_CHANGED = "COMPLETED_WEEK_CHANGED"
    TARGET_WEEK_CHANGED = "TARGET_WEEK_CHANGED"
    ACTIVE_TIER_CHANGED = "ACTIVE_TIER_CHANGED"
    ROSTER_CHANGED = "ROSTER_CHANGED"
    ROSTER_ORDER_CHANGED = "ROSTER_ORDER_CHANGED"
    MAIN_BINDING_CHANGED = "MAIN_BINDING_CHANGED"
    ALT_BINDING_CHANGED = "ALT_BINDING_CHANGED"
    CHARACTER_CHANGED = "CHARACTER_CHANGED"
    JOB_CHANGED = "JOB_CHANGED"
    BIS_CHANGED = "BIS_CHANGED"
    BIS_ENTRY_CHANGED = "BIS_ENTRY_CHANGED"
    GEAR_CHANGED = "GEAR_CHANGED"
    INVENTORY_CHANGED = "INVENTORY_CHANGED"
    MATERIAL_INVENTORY_CHANGED = "MATERIAL_INVENTORY_CHANGED"
    MATERIAL_GRANT_CHANGED = "MATERIAL_GRANT_CHANGED"
    FLOOR_CONFIGURATION_CHANGED = "FLOOR_CONFIGURATION_CHANGED"
    LOOT_CONFIGURATION_CHANGED = "LOOT_CONFIGURATION_CHANGED"
    SNAPSHOT_MISSING = "SNAPSHOT_MISSING"
    SNAPSHOT_VERSION_UNSUPPORTED = "SNAPSHOT_VERSION_UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class LootPlanStaleReason:
    code: LootPlanStaleReasonCode
    message: str


@dataclass(frozen=True, slots=True)
class LootPlanStalenessResult:
    state: LootPlanStalenessState
    reasons: tuple[LootPlanStaleReason, ...] = field(default_factory=tuple)

    @property
    def confirmation_blocked(self) -> bool:
        return self.state is not LootPlanStalenessState.CURRENT


@dataclass(frozen=True, slots=True)
class PersistedLootParticipant:
    character_id: int
    character_name: str
    world: str
    job: str
    designation: CharacterKind
    roster_order: int


@dataclass(frozen=True, slots=True)
class PersistedLootAssignment:
    assignment_id: int
    floor_number: int
    floor_name: str
    loot_label: str
    disposition: PlannedLootDisposition
    recipient_id: int | None
    recipient_name: str | None
    recipient_job: str | None
    recipient_designation: CharacterKind | None
    expected_drop_instance: int
    paired_assignment_id: int | None = None


@dataclass(frozen=True, slots=True)
class PersistedLootRun:
    run_id: int
    run_number: int
    name: str
    participants: tuple[PersistedLootParticipant, ...]
    assignments: tuple[PersistedLootAssignment, ...]


@dataclass(frozen=True, slots=True)
class PersistedLootPlanResult:
    plan_id: int
    static_id: int
    static_name: str
    tier_id: int
    tier_name: str
    target_week: int
    mode: ClearMode
    status: WeeklyLootPlanStatus
    creator_discord_user_id: int | None
    created_at: datetime
    runs: tuple[PersistedLootRun, ...]
    validation_warnings: tuple[str, ...] = field(default_factory=tuple)
    applied_at: datetime | None = None
    cancelled_at: datetime | None = None
    snapshot_version: int | None = None
    stored_source_hash: str | None = None
    staleness: LootPlanStalenessState = LootPlanStalenessState.UNVERIFIABLE
    stale_reasons: tuple[LootPlanStaleReason, ...] = field(default_factory=tuple)
    confirmation_blocked: bool = True


class LootPlanPersistenceError(ValueError):
    """Base error for invalid or unavailable generated plans."""


class LootPlanValidationError(LootPlanPersistenceError):
    """The planner result cannot be represented safely by the persistence model."""


class ActiveLootPlanError(LootPlanPersistenceError):
    """A DRAFT or READY plan already targets the requested week."""


class PersistedLootPlanNotFound(LootPlanPersistenceError):
    """The requested persisted plan does not exist."""


class ActiveLootPlanConflict(LootPlanPersistenceError):
    """Multiple active plans exist for one planning scope."""


class LootPlanAlreadyCancelled(LootPlanPersistenceError):
    """The requested plan is already cancelled."""
