"""Transport-independent reclear confirmation results."""

from dataclasses import dataclass

from app.models import ConfirmationQuestion, LootAssignment


@dataclass(frozen=True, slots=True)
class ConfirmationQueueItem:
    assignment: LootAssignment
    question: ConfirmationQuestion


@dataclass(frozen=True, slots=True)
class ConfirmationProgress:
    total_planned_assignments: int
    fully_resolved_assignments: int
    pending_receipt_questions: int
    pending_redemption_questions: int
    pending_augmentation_questions: int
    failed_assignments: int
    leftovers: int
    can_close: bool


class ConfirmationError(ValueError):
    """Raised when a confirmation violates the loot state machine."""
