"""Command-layer resolution helpers for current gear and resources."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Character,
    CharacterKind,
    GearClassification,
    GearSlot,
    Static,
    StaticMember,
)
from app.services.gear import CURRENT_CLASSIFICATIONS, resolve_character


def character(session: Session, static: Static, value: str):
    name, separator, world = value.partition("@")
    return resolve_character(session, static, name.strip(), world.strip() if separator else None)


def member_character(
    session: Session, static: Static, display_name: str, main_or_alt: str
) -> tuple[StaticMember, Character]:
    """Resolve a member and exactly one of their characters inside ``static``."""
    try:
        kind = CharacterKind[main_or_alt.strip().upper()]
    except KeyError as exc:
        raise ValueError("Choose Main or Alt.") from exc

    value = display_name.strip()
    member = None
    if value.isdigit():
        member = session.scalar(
            select(StaticMember).where(
                StaticMember.id == int(value),
                StaticMember.static_id == static.id,
                StaticMember.active.is_(True),
            )
        )
    else:
        matches = list(
            session.scalars(
                select(StaticMember).where(
                    StaticMember.static_id == static.id,
                    StaticMember.active.is_(True),
                    StaticMember.display_name.ilike(value),
                )
            )
        )
        if len(matches) == 1:
            member = matches[0]

    if member is None:
        raise ValueError("That member is not in the selected static.")
    character = session.scalar(
        select(Character).where(
            Character.static_member_id == member.id,
            Character.kind == kind,
            Character.active.is_(True),
        )
    )
    if character is None:
        raise ValueError(f"That member has no active {kind.value.title()} character.")
    return member, character


def slot(session: Session, value: str) -> GearSlot:
    normalized = value.strip().upper().replace(" ", "_")
    row = session.scalar(select(GearSlot).where(GearSlot.code == normalized))
    if row is None:
        raise ValueError(f"Unknown gear slot: {value}.")
    return row


def classification(value: str, target_slot: GearSlot | None = None) -> GearClassification:
    try:
        result = GearClassification[value.strip().upper()]
    except KeyError as exc:
        raise ValueError(f"Unknown current classification: {value}.") from exc
    if result not in CURRENT_CLASSIFICATIONS:
        raise ValueError("NOT_APPLICABLE is only valid for desired BiS configuration.")
    return result
