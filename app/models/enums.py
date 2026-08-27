"""Stable domain enumerations persisted by name."""

from enum import StrEnum


class CharacterKind(StrEnum):
    MAIN = "MAIN"
    ALT = "ALT"


class GearClassification(StrEnum):
    SAVAGE = "SAVAGE"
    AUGMENTED_TOME = "AUGMENTED_TOME"
    TOME = "TOME"
    CRAFTED = "CRAFTED"
    EX_WEAPON = "EX_WEAPON"
    GARBAGE = "GARBAGE"
    CATCHUP = "CATCHUP"
    RELIC = "RELIC"
    NORMAL_RAID = "NORMAL_RAID"
    EITHER = "EITHER"
    OTHER = "OTHER"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GearSlotCode(StrEnum):
    WEAPON = "WEAPON"
    OFFHAND = "OFFHAND"
    HEAD = "HEAD"
    BODY = "BODY"
    HANDS = "HANDS"
    LEGS = "LEGS"
    FEET = "FEET"
    EARRINGS = "EARRINGS"
    NECKLACE = "NECKLACE"
    BRACELETS = "BRACELETS"
    RING_1 = "RING_1"
    RING_2 = "RING_2"


def job_uses_offhand(job_abbreviation: str) -> bool:
    """Return whether an FFXIV combat job equips a separate offhand item."""
    return job_abbreviation.upper() == "PLD"


class LootAssignmentState(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    RECEIVED = "RECEIVED"
    REDEEMED_CORRECTLY = "REDEEMED_CORRECTLY"
    RECEIPT_FAILED = "RECEIPT_FAILED"
    REDEMPTION_ERROR = "REDEMPTION_ERROR"
    LEFTOVER = "LEFTOVER"
    FREE_ROLL = "FREE_ROLL"
    CANCELLED = "CANCELLED"


class LootPlanState(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"


class LootCategory(StrEnum):
    GEAR = "GEAR"
    COFFER = "COFFER"
    AUGMENTATION_MATERIAL = "AUGMENTATION_MATERIAL"
    MOUNT = "MOUNT"
    OTHER = "OTHER"


class ClearMode(StrEnum):
    REGULAR = "REGULAR"
    SPLIT = "SPLIT"


class ReclearWorkflowState(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class LootConfirmationType(StrEnum):
    RECEIVED = "RECEIVED"
    REDEEMED_CORRECTLY = "REDEEMED_CORRECTLY"
    AUGMENT_APPLIED = "AUGMENT_APPLIED"


class DistributionErrorType(StrEnum):
    INTENDED_RECIPIENT_DID_NOT_RECEIVE = "INTENDED_RECIPIENT_DID_NOT_RECEIVE"
    WRONG_RECIPIENT = "WRONG_RECIPIENT"
    WRONG_COFFER_REDEMPTION = "WRONG_COFFER_REDEMPTION"
    AUGMENT_NOT_APPLIED = "AUGMENT_NOT_APPLIED"
    USER_ENTRY_ERROR = "USER_ENTRY_ERROR"
    OTHER = "OTHER"


class ConfirmationQuestion(StrEnum):
    RECEIVED = "RECEIVED"
    REDEEMED_CORRECTLY = "REDEEMED_CORRECTLY"
    AUGMENT_APPLIED = "AUGMENT_APPLIED"
