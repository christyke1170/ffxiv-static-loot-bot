"""Authoritative gear-board status classification."""

from app.models import GearClassification, GearSlotCode
from app.schemas.board import DisplayStatus
from app.schemas.needs_v2 import NeedsV2SlotResult, NeedsV2Status


def classify_gear_state(result: NeedsV2SlotResult) -> DisplayStatus:
    """Classify one displayed slot using its current gear state.

    The needs engine remains authoritative for category equality and manual completion.
    """
    job = getattr(result.character, "job", None)
    if result.slot.code is GearSlotCode.OFFHAND and job is not None and not job.uses_offhand:
        return DisplayStatus.NA

    # Completion is deliberately checked before looking at the current source.
    # A completed exact item must not be relabeled just because it was entered as
    # Crafted, EX, Tome, or another current-gear source.
    if result.status in {NeedsV2Status.COMPLETE, NeedsV2Status.MANUALLY_COMPLETE}:
        return DisplayStatus.BIS
    if result.status is NeedsV2Status.INVALID_CONFIGURATION:
        return DisplayStatus.NEEDS_REPLACEMENT

    desired = result.desired_classification
    current = result.current_classification
    if desired is current and desired in {
        GearClassification.CRAFTED_EX,
        GearClassification.SAVAGE,
        GearClassification.TOME,
        GearClassification.AUGMENTED_TOME,
    }:
        return DisplayStatus.BIS
    if current is GearClassification.CRAFTED_EX:
        return DisplayStatus.CRAFTED_EX

    if current in {GearClassification.SAVAGE, GearClassification.AUGMENTED_TOME}:
        return DisplayStatus.ALTERNATE
    if current is GearClassification.TOME:
        return DisplayStatus.TOME_NEEDS_AUGMENT

    return DisplayStatus.NEEDS_REPLACEMENT
