"""Authoritative gear-board status classification."""

from app.models import GearClassification, GearSlotCode
from app.schemas.board import DisplayStatus
from app.schemas.needs import NeedStatus, SlotNeedResult


def classify_gear_state(result: SlotNeedResult) -> DisplayStatus:
    """Classify one displayed slot using its current gear state.

    The needs engine remains authoritative for exact desired-item ownership and
    manual completion. Every other decision is based on the persisted current
    source classification, never its tier, level, or inferred item name.
    """
    job = getattr(getattr(result.character, "job", None), "abbreviation", "")
    if result.slot.code is GearSlotCode.OFFHAND and job.upper() != "PLD":
        return DisplayStatus.NA

    # Completion is deliberately checked before looking at the current source.
    # A completed exact item must not be relabeled just because it was entered as
    # Crafted, EX, Tome, or another current-gear source.
    if result.status in {NeedStatus.COMPLETE, NeedStatus.MANUALLY_COMPLETE}:
        return DisplayStatus.BIS
    if result.status is NeedStatus.INVALID_CONFIGURATION:
        return DisplayStatus.NEEDS_REPLACEMENT

    desired = result.desired_classification
    current = result.current_classification
    if desired is current and desired in {
        GearClassification.CRAFTED,
        GearClassification.EX_WEAPON,
        GearClassification.SAVAGE,
        GearClassification.TOME,
        GearClassification.AUGMENTED_TOME,
    }:
        return DisplayStatus.BIS
    if current is GearClassification.CRAFTED or current is GearClassification.EX_WEAPON:
        return DisplayStatus.CRAFTED_EX

    if current in {GearClassification.SAVAGE, GearClassification.AUGMENTED_TOME}:
        return DisplayStatus.ALTERNATE
    if current is GearClassification.TOME:
        return DisplayStatus.TOME_NEEDS_AUGMENT

    return DisplayStatus.NEEDS_REPLACEMENT
