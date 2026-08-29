"""Database boundary for the side-by-side tier-neutral needs calculator."""

from sqlalchemy.orm import Session

from app.schemas.needs_v2 import CharacterNeedsResult
from app.services.needs_calculator import calculate_needs_from_state
from app.services.needs_state import load_characters_needs_states


def calculate_character_needs_v2(session: Session, character_id: int) -> CharacterNeedsResult:
    """Load and calculate one character's neutral needs without writing."""
    state = load_characters_needs_states(session, (character_id,))[0]
    return calculate_needs_from_state(state)


def calculate_characters_needs_v2(
    session: Session, character_ids
) -> tuple[CharacterNeedsResult, ...]:
    """Calculate V2 needs for characters in input order without per-character loads."""
    return tuple(
        calculate_needs_from_state(state)
        for state in load_characters_needs_states(session, character_ids)
    )
