"""Typed, transport-independent service results."""

from app.schemas.confirmations import ConfirmationError, ConfirmationProgress, ConfirmationQueueItem
from app.schemas.needs import (
    AugmentationNeed,
    BookAvailability,
    BookRequirement,
    CharacterNeedsResult,
    MaterialOwnership,
    NeedStatus,
    OwnedCofferAvailability,
    SavageLootNeed,
    SlotNeedResult,
)
from app.schemas.planning import (
    LootPlanGenerationError,
    PlannedDropResult,
    PlanWarning,
    RankedEligibleRecipient,
    RosterValidationResult,
    ValidationIssue,
    ValidationIssueCode,
    WeeklyPlanResult,
)

__all__ = [
    "AugmentationNeed",
    "BookAvailability",
    "BookRequirement",
    "CharacterNeedsResult",
    "MaterialOwnership",
    "NeedStatus",
    "OwnedCofferAvailability",
    "SavageLootNeed",
    "SlotNeedResult",
    "LootPlanGenerationError",
    "PlannedDropResult",
    "PlanWarning",
    "RankedEligibleRecipient",
    "RosterValidationResult",
    "ValidationIssue",
    "ValidationIssueCode",
    "WeeklyPlanResult",
    "ConfirmationError",
    "ConfirmationProgress",
    "ConfirmationQueueItem",
]
