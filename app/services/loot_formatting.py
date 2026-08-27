"""Discord-safe fixed-width weekly loot-board formatting."""

from app.schemas.reclear import LootBoard, LootBoardRow
from app.services.formatting import _bounded_codeblock, _row, safe_text

LOOT_ROWS_PER_PAGE = 12


def loot_board_table(
    board: LootBoard, *, floor_id: int | None = None, group: int | None = None, page: int = 0
) -> tuple[str, int]:
    rows = [
        row
        for row in board.rows
        if (floor_id is None or row.floor_id == floor_id)
        and (group is None or row.group_number == group)
    ]
    page_count = max((len(rows) + LOOT_ROWS_PER_PAGE - 1) // LOOT_ROWS_PER_PAGE, 1)
    page = min(max(page, 0), page_count - 1)
    selected = rows[page * LOOT_ROWS_PER_PAGE : (page + 1) * LOOT_ROWS_PER_PAGE]
    lines = [
        _row(("Floor", "Split", "Drop", "Recipient", "Backup", "Status"), (12, 5, 18, 14, 14, 18))
    ]
    lines.extend(
        _row(
            (
                row.floor_name,
                chr(64 + row.group_number),
                row.drop_name,
                row.recipient,
                row.backup,
                row.status.value.replace("_", " ").title(),
            ),
            (12, 5, 18, 14, 14, 18),
        )
        for row in selected
    )
    if not selected:
        lines.append("No planned assignments match this filter.")
    return _bounded_codeblock(lines), page_count


def assignment_detail(row: LootBoardRow) -> str:
    history = (
        "\n".join(
            f"- {kind.value}: {'Yes' if result else 'No'}{f' — {safe_text(note)}' if note else ''}"
            for kind, result, note in row.confirmations
        )
        or "- None"
    )
    errors = "\n".join(f"- {safe_text(value)}" for value in row.distribution_errors) or "- None"
    base = "N/A" if row.base_tome_owned is None else ("Yes" if row.base_tome_owned else "No")
    text = (
        f"**{safe_text(row.floor_name)} — Split {chr(64 + row.group_number)}**\n"
        f"Drop: {safe_text(row.drop_name)} #{row.instance}\n"
        f"Intended: {safe_text(row.intended_slot)} / {safe_text(row.intended_item)}\n"
        f"Suggested: {safe_text(row.suggested_recipient)}\n"
        f"Final: {safe_text(row.final_recipient or row.recipient)}\n"
        f"Backup: {safe_text(row.backup)}\nHierarchy: {row.hierarchy_position or '—'}\n"
        f"Reason: {safe_text(row.reason)}\nBase tome owned: {base}\nState: {row.status.value}\n"
        f"**Confirmation history**\n{history}\n**Distribution errors**\n{errors}"
    )
    return text[:2000]
