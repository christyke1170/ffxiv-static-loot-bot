"""Discord-safe fixed-width gear-board formatting."""

import unicodedata
from collections.abc import Sequence

from app.models import GearClassification
from app.schemas.board import BoardPlayer, DisplayStatus, StaticGearBoard
from app.schemas.needs import NeedStatus

DISCORD_TEXT_LIMIT = 2000
PLAYERS_PER_PAGE = 4

SLOT_LABEL = {
    "WEAPON": "Weapon",
    "OFFHAND": "Offhand",
    "HEAD": "Hat",
    "BODY": "Chest",
    "HANDS": "Gloves",
    "LEGS": "Pants",
    "FEET": "Boots",
    "EARRINGS": "Earring",
    "NECKLACE": "Necklace",
    "BRACELETS": "Bracelet",
    "RING_1": "Ring 1",
    "RING_2": "Ring 2",
}
SUMMARY_SLOT_LABEL = {
    "WEAPON": "Weapon",
    "OFFHAND": "Offhand",
    "HEAD": "Heads",
    "BODY": "Bodies",
    "HANDS": "Gloves",
    "LEGS": "Pants",
    "FEET": "Boots",
    "EARRINGS": "Earrings",
    "NECKLACE": "Necklaces",
    "BRACELETS": "Bracelets",
    "RING_1": "Rings",
    "RING_2": "Rings",
}
MATERIAL_LABEL = {"ACCESSORY_GLAZE": "Glaze", "ARMOR_TWINE": "Twine", "GEAR_TWINE": "Twine"}
LOOT_TYPE_LABEL = {
    "ACCESSORY_COFFER": "Accessories",
    "HEAD_COFFER": "Heads",
    "WEAPON_COFFER": "Weapons",
    "WEAPON": "Weapons",
}
STATUS_SYMBOL = {
    DisplayStatus.BIS: "🟩",
    DisplayStatus.ALTERNATE: "🟦",
    DisplayStatus.TOME_NEEDS_AUGMENT: "🟧",
    DisplayStatus.CRAFTED_EX: "🟨",
    DisplayStatus.NA: "⬛",
    DisplayStatus.NEEDS_REPLACEMENT: "🟥",
}
STATUS_LABEL = {
    DisplayStatus.BIS: "BiS",
    DisplayStatus.ALTERNATE: "Alternate",
    DisplayStatus.TOME_NEEDS_AUGMENT: "Tome needs augment",
    DisplayStatus.CRAFTED_EX: "Crafted / EX",
    DisplayStatus.NA: "N/A",
    DisplayStatus.NEEDS_REPLACEMENT: "Needs replacement",
}
CLASSIFICATION_LABEL = {
    GearClassification.SAVAGE: "Savage",
    GearClassification.AUGMENTED_TOME: "Tome Up",
    GearClassification.TOME: "Tome",
    GearClassification.CRAFTED: "Crafted",
    GearClassification.EX_WEAPON: "EX weapon",
    GearClassification.GARBAGE: "Garbage",
    GearClassification.CATCHUP: "Catchup",
    GearClassification.RELIC: "Relic",
    GearClassification.NORMAL_RAID: "Normal",
    GearClassification.EITHER: "Either",
    GearClassification.OTHER: "Other",
    GearClassification.NOT_APPLICABLE: "N/A",
}
LEGEND = (
    "🟩 BiS  🟦 Alternate  🟧 Tome needs augment  🟨 Crafted / EX  ⬛ N/A  🟥 Needs replacement"
)
OVERVIEW_COLUMN_WIDTH = 16
OVERVIEW_SLOT_PAIRS = (
    ("WEAPON", "OFFHAND"),
    ("HEAD", "EARRINGS"),
    ("BODY", "NECKLACE"),
    ("HANDS", "BRACELETS"),
    ("LEGS", "RING_1"),
    ("FEET", "RING_2"),
)


def overview_table(board: StaticGearBoard, page: int = 0) -> tuple[str, tuple[str, ...]]:
    page_count = max((len(board.players) + PLAYERS_PER_PAGE - 1) // PLAYERS_PER_PAGE, 1)
    page = min(max(page, 0), page_count - 1)
    players = board.players[page * PLAYERS_PER_PAGE : (page + 1) * PLAYERS_PER_PAGE]
    warnings = list(board.warnings)
    for player in players:
        warnings.extend(player.warnings)
    if not players:
        return "No active main characters.", tuple(dict.fromkeys(warnings))
    lines = []
    for index, player in enumerate(players):
        if index:
            lines.append("")
        name = truncate(player.display_name, 30)
        kind = truncate(player.character_kind.title(), 8)
        job = truncate(player.job or "?", 8)
        lines.append(f"{name} · {job} · {kind}")
        lines.append(
            f"{player.complete_slots}/{player.applicable_slots} complete | "
            f"Savage {_remaining_classification_needs(player, GearClassification.SAVAGE)} | "
            f"Augment {_remaining_augmentation_needs(player)} | "
            f"Tome {_remaining_tome_needs(player)}"
        )
        lines.append("")
        lines.extend(_slot_lines(player))
    return _bounded_codeblock(lines), tuple(dict.fromkeys(warnings))


def _slot_lines(player: BoardPlayer) -> list[str]:
    slots = {slot.code.value: slot for slot in player.slots}
    return [
        "  ".join(
            _pad(_slot_label(slots[code]) if code in slots else "", OVERVIEW_COLUMN_WIDTH)
            for code in pair
        )
        for pair in OVERVIEW_SLOT_PAIRS
    ]


def _slot_label(slot) -> str:
    label = SLOT_LABEL.get(slot.code.value, truncate(slot.name, 6))
    return f"{STATUS_SYMBOL[slot.display_status]} {label}"


def _remaining_classification_needs(player: BoardPlayer, classification: GearClassification) -> int:
    return sum(
        slot.display_status not in {DisplayStatus.BIS, DisplayStatus.NA}
        and slot.desired_classification is classification
        for slot in player.slots
    )


def _remaining_augmentation_needs(player: BoardPlayer) -> int:
    return sum(slot.display_status is DisplayStatus.TOME_NEEDS_AUGMENT for slot in player.slots)


def _remaining_tome_needs(player: BoardPlayer) -> int:
    return sum(
        slot.display_status not in {DisplayStatus.BIS, DisplayStatus.NA}
        and slot.desired_classification
        in {GearClassification.TOME, GearClassification.AUGMENTED_TOME}
        for slot in player.slots
    )


def player_table(player: BoardPlayer) -> tuple[str, tuple[str, ...]]:
    lines = [_row(("Slot", "Desired", "Current", "Status"), (10, 12, 12, 18))]
    for slot in player.slots:
        lines.append(
            _row(
                (
                    slot.name,
                    classification_label(slot.desired_classification),
                    classification_label(slot.current_classification),
                    f"{STATUS_SYMBOL[slot.display_status]} {STATUS_LABEL[slot.display_status]}",
                ),
                (10, 12, 12, 18),
            )
        )
    return _bounded_codeblock(lines), player.warnings


def player_books(player: BoardPlayer) -> str:
    """Format effective, currently spendable books for a player detail view."""
    lines = ["**Books**"]
    lines.extend(f"Floor {book.floor_number} Books: {book.available}" for book in player.books)
    return "\n".join(lines)


def summary_table(board: StaticGearBoard) -> tuple[str, tuple[str, ...]]:
    warnings = list(board.warnings)
    total_complete = sum(player.complete_slots for player in board.players)
    total_applicable = sum(player.applicable_slots for player in board.players)
    lines = [
        "Static Progress",
        f"{total_complete} / {total_applicable} slots complete",
        "",
        "Current Week",
        f"Week {board.current_week or 2}",
        "",
        "Player Progress",
    ]
    if board.players:
        width = max(len(player.display_name) for player in board.players)
        lines.extend(
            f"{player.display_name.ljust(width)}  {player.complete_slots}/{player.applicable_slots}"
            for player in board.players
        )
    else:
        lines.append("None")
    lines.extend(["", "Remaining Savage Drops"])
    savage: dict[tuple[int, str], int] = {}
    for player in board.players:
        for slot in player.slots:
            if (
                slot.needs_status is NeedStatus.NEEDS_SAVAGE_DROP
                and slot.required_floor_number is not None
            ):
                label = loot_type_label(slot.required_loot_type_code, slot.code.value)
                savage[(slot.required_floor_number, label)] = (
                    savage.get((slot.required_floor_number, label), 0) + 1
                )
    for floor in sorted({floor for floor, _ in savage}):
        drops = [
            (label, quantity)
            for (drop_floor, label), quantity in savage.items()
            if drop_floor == floor
        ]
        lines.append(
            f"Floor {floor}: "
            + " · ".join(f"{label} {quantity}" for label, quantity in sorted(drops))
        )
    if not savage:
        lines.append("None")
    lines.extend(["", "Augmentation Needed"])
    materials: dict[str, int] = {}
    for player in board.players:
        for material in player.materials:
            if material.needed:
                label = material_label(material.code, material.name)
                materials[label] = materials.get(label, 0) + material.needed
    if materials:
        lines.extend(f"{label}: {quantity}" for label, quantity in sorted(materials.items()))
    else:
        lines.append("None")
    lines.extend(["", "Books Per Player"])
    floors = sorted({book.floor_number for player in board.players for book in player.books})
    exceptions = []
    for floor in floors:
        values = {
            player.display_name: next(
                book.available for book in player.books if book.floor_number == floor
            )
            for player in board.players
        }
        common = next(iter(values.values()), 0)
        lines.append(f"Floor {floor}: {common}")
        exceptions.extend(
            f"{name}: Floor {floor} = {value}" for name, value in values.items() if value != common
        )
    if not floors:
        lines.append("None")
    if exceptions:
        lines.extend(["", "Book Exceptions", *exceptions])
    lines.append("Books are individual character balances, not a pooled static currency.")
    for player in board.players:
        warnings.extend(player.warnings)
    return _bounded_codeblock(lines), tuple(dict.fromkeys(warnings))


def classification_label(value: GearClassification | None) -> str:
    return CLASSIFICATION_LABEL.get(value, "Unknown")


def material_label(code: str, name: str | None = None) -> str:
    if code in MATERIAL_LABEL:
        return MATERIAL_LABEL[code]
    words = (name or code).replace("_", " ").split()
    return " ".join(word.capitalize() for word in words[-3:])


def loot_type_label(value: str | None, slot_code: str | None = None) -> str:
    """Return a readable configured loot label without exposing internal codes."""
    if value in LOOT_TYPE_LABEL:
        return LOOT_TYPE_LABEL[value]
    if slot_code in SUMMARY_SLOT_LABEL:
        return SUMMARY_SLOT_LABEL[slot_code]
    words = (value or "Savage drop").replace("_", " ").split()
    return " ".join(word.capitalize() for word in words[-3:])


def safe_text(value: object) -> str:
    return str(value).replace("`", "ˋ").replace("\r", " ").replace("\n", " ").replace("@", "＠")


def text_width(value: str) -> int:
    return sum(
        2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1 for character in value
    )


def truncate(value: object, width: int) -> str:
    text = safe_text(value)
    if text_width(text) <= width:
        return text
    result = ""
    for character in text:
        if text_width(result + character + "…") > width:
            break
        result += character
    return result + "…"


def _pad(value: object, width: int) -> str:
    text = truncate(value, width)
    return text + " " * max(width - text_width(text), 0)


def _row(values: Sequence[object], widths: Sequence[int]) -> str:
    return " ".join(
        _pad(value, width) for value, width in zip(values, widths, strict=True)
    ).rstrip()


def _heading(player: BoardPlayer) -> str:
    return f"{player.display_name} ({player.job or '?'})"


def _bounded_codeblock(lines: list[str]) -> str:
    prefix, suffix = "```text\n", "\n```"
    selected = []
    size = len(prefix) + len(suffix)
    for line in lines:
        addition = len(line) + (1 if selected else 0)
        if size + addition > DISCORD_TEXT_LIMIT:
            marker = "… table truncated"
            while selected and size + len(marker) + 1 > DISCORD_TEXT_LIMIT:
                removed = selected.pop()
                size -= len(removed) + (1 if selected else 0)
            selected.append(marker)
            break
        selected.append(line)
        size += addition
    return prefix + "\n".join(selected) + suffix
