"""Optional job icons at presentation boundaries; job keys remain plain text."""

from collections.abc import Mapping


def job_label(abbreviation: str | None, icons: Mapping | None = None) -> str:
    code = abbreviation or "?"
    emoji = icons.get(code.upper()) if icons else None
    return f"{emoji} {code}" if emoji else code
