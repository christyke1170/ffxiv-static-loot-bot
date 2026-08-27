"""Discord-independent results and errors for persisted loot-plan confirmation."""

from dataclasses import dataclass, field
from datetime import datetime

from app.models import WeeklyLootPlanStatus


@dataclass(frozen=True, slots=True)
class LootPlanConfirmationResult:
    plan_id: int
    previous_status: WeeklyLootPlanStatus
    resulting_status: WeeklyLootPlanStatus
    changes_applied: bool
    already_applied: bool
    savage_gear_update_count: int
    twine_grant_count: int
    glaze_grant_count: int
    paired_alt_weapon_upgrade_count: int
    book_increment_count: int
    clear_record_count: int
    previous_completed_week: int
    resulting_completed_week: int
    applied_at: datetime | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class LootPlanConfirmationError(ValueError):
    """Base error for plans that cannot be safely confirmed."""


class LootPlanNotReadyError(LootPlanConfirmationError):
    """The plan is not READY for first application."""


class LootPlanStaleError(LootPlanConfirmationError):
    """The plan source snapshot is stale or unverifiable."""


class LootPlanIntegrityError(LootPlanConfirmationError):
    """The persisted plan graph is corrupt or internally inconsistent."""


class LootPlanWeekConflictError(LootPlanConfirmationError):
    """The persisted plan no longer targets the next reclear week."""
