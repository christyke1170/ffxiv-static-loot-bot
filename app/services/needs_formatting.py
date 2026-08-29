"""Compact Discord-safe formatting for neutral needs results."""

from app.schemas.needs_v2 import CharacterNeedsResult, NeedsV2Status


def format_needs_player(result: CharacterNeedsResult) -> str:
    """Render one character's category-only needs without persistence details."""
    lines = [
        f"{_safe(result.character_name)} â€” {_safe(result.job_abbreviation or '?')}",
        f"Complete: {result.complete_slot_count}/{result.applicable_slot_count} applicable slots",
        f"Full BiS: {'yes' if result.full_bis else 'no'}",
    ]
    remaining = [
        f"{_safe(row.slot_name)}: {_status(row.status)}"
        for row in result.slot_results
        if row.status
        not in {
            NeedsV2Status.COMPLETE,
            NeedsV2Status.NOT_APPLICABLE,
            NeedsV2Status.MANUALLY_COMPLETE,
        }
    ]
    if remaining:
        lines.extend(["", "Remaining slots", *remaining])
    if result.savage_needs:
        lines.extend(
            [
                "",
                "Savage needs",
                *(
                    f"Floor {row.floor_number} {_safe(row.loot_type_code)}: {row.quantity}"
                    for row in result.savage_needs
                ),
            ]
        )
    if result.material_needs:
        lines.extend(
            [
                "",
                "Augmentation materials",
                *(
                    f"{_safe(row.material_name)}: owned {row.owned}, "
                    f"allocated {row.allocated}, additionally needed {row.additional_needed}"
                    for row in result.material_needs
                ),
            ]
        )
    if result.book_balances:
        lines.extend(
            [
                "",
                "Book balances (recorded manually)",
                *(f"Floor {row.floor_number}: {row.available}" for row in result.book_balances),
            ]
        )
    if result.coffer_summaries:
        lines.extend(
            [
                "",
                "Matching unopened coffers",
                *(
                    f"{_safe(row.loot_type_code)}: owned {row.owned}, allocated {row.allocated}"
                    for row in result.coffer_summaries
                ),
            ]
        )
    if result.configuration_warnings:
        lines.extend(
            ["", "Warnings", *(f"- {_safe(row)}" for row in result.configuration_warnings)]
        )
    return "\n".join(lines)[:2000]


def _status(status: NeedsV2Status) -> str:
    return status.value.replace("_", " ").title()


def _safe(value: object) -> str:
    return str(value).replace("`", "Ë‹").replace("@", "ï¼ ")
