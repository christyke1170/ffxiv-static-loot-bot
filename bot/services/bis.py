"""Administrator-managed Static + Job BiS operations."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    BisSet,
    BisSetItem,
    Character,
    CharacterKind,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Job,
    Static,
)

ALLOWED_DESIRED_CATEGORIES = frozenset(
    {
        GearClassification.CRAFTED_EX,
        GearClassification.TOME,
        GearClassification.AUGMENTED_TOME,
        GearClassification.SAVAGE,
        GearClassification.NOT_APPLICABLE,
    }
)


@dataclass(frozen=True, slots=True)
class BisSummary:
    static: Static
    job: Job
    bis_set: BisSet | None
    main_count: int
    alt_count: int


def resolve_job(session: Session, value: str) -> Job:
    normalized = value.strip().upper()
    job = session.scalar(
        select(Job).where(
            (func.upper(Job.abbreviation) == normalized) | (func.upper(Job.name) == normalized)
        )
    )
    if job is None:
        if session.scalar(select(func.count()).select_from(Job)) == 0:
            raise ValueError("Reference data is missing; an administrator must run `/setup seed`.")
        raise ValueError(f"Unknown job: {value}.")
    return job


def load_bis(session: Session, static_id: int, job_id: int) -> BisSet | None:
    return session.scalar(
        select(BisSet)
        .where(BisSet.static_id == static_id, BisSet.job_id == job_id, BisSet.active.is_(True))
        .order_by(BisSet.id)
    )


def validate_categories(
    session: Session, job: Job, categories: dict[GearSlotCode, GearClassification]
) -> list[GearSlot]:
    slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
    expected = {slot.code for slot in slots}
    if set(categories) != expected:
        missing = sorted(expected - set(categories), key=lambda code: code.value)
        raise ValueError(
            "Every gear slot must have a desired category; missing " + ", ".join(missing)
        )
    invalid = set(categories.values()) - ALLOWED_DESIRED_CATEGORIES
    if invalid:
        raise ValueError(
            "Invalid desired BiS category: " + ", ".join(sorted(v.value for v in invalid))
        )
    if (
        not job.uses_offhand
        and categories[GearSlotCode.OFFHAND] is not GearClassification.NOT_APPLICABLE
    ):
        raise ValueError("Offhand must be NOT_APPLICABLE for jobs without Offhand capability.")
    invalid_na = {
        slot.code
        for slot in slots
        if slot.code is not GearSlotCode.OFFHAND
        and categories[slot.code] is GearClassification.NOT_APPLICABLE
    }
    if invalid_na:
        raise ValueError("NOT_APPLICABLE is only valid for a non-offhand-capable Offhand slot.")
    return slots


def save_bis(
    session: Session,
    static: Static,
    job: Job,
    categories: dict[GearSlotCode, GearClassification],
    actor_id: int,
) -> BisSet:
    slots = validate_categories(session, job, categories)
    row = load_bis(session, static.id, job.id)
    if row is None:
        row = BisSet(
            static=static,
            job=job,
            name=f"{job.abbreviation} BiS",
            active=True,
        )
        session.add(row)
        session.flush()
    else:
        existing = {item.gear_slot_id: item.classification for item in row.items}
        if existing == {slot.id: categories[slot.code] for slot in slots}:
            return row
        row.items.clear()
        session.flush()
    row.items = [BisSetItem(gear_slot=slot, classification=categories[slot.code]) for slot in slots]
    session.add(
        AuditLog(
            static_id=static.id,
            actor_discord_user_id=actor_id,
            action="STATIC_JOB_BIS_SAVED",
            entity_type="BisSet",
            entity_id=str(row.id),
            details=f"job={job.abbreviation}",
        )
    )
    session.flush()
    return row


def summarize_bis(session: Session, static: Static, job: Job) -> BisSummary:
    row = load_bis(session, static.id, job.id)
    main_count = alt_count = 0
    if row is not None:
        main_count = (
            session.scalar(
                select(func.count(Character.id))
                .join(Character.static_member)
                .where(
                    Character.job_id == job.id,
                    Character.kind == CharacterKind.MAIN,
                    Character.active.is_(True),
                    Character.static_member.has(static_id=static.id, active=True),
                )
            )
            or 0
        )
        alt_count = (
            session.scalar(
                select(func.count(Character.id))
                .join(Character.static_member)
                .where(
                    Character.job_id == job.id,
                    Character.kind == CharacterKind.ALT,
                    Character.active.is_(True),
                    Character.static_member.has(static_id=static.id, active=True),
                )
            )
            or 0
        )
    return BisSummary(static, job, row, main_count, alt_count)


def clear_bis(session: Session, static: Static, job: Job, actor_id: int) -> bool:
    row = load_bis(session, static.id, job.id)
    if row is None:
        return False
    row.active = False
    session.add(
        AuditLog(
            static_id=static.id,
            actor_discord_user_id=actor_id,
            action="STATIC_JOB_BIS_CLEARED",
            entity_type="BisSet",
            entity_id=str(row.id),
            details=f"job={job.abbreviation}",
        )
    )
    return True
