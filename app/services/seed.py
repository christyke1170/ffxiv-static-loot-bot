from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.jobs import JOB_CATALOG
from app.models import GearSlot, GearSlotCode, Job
from app.services.hierarchy import bootstrap_default_hierarchies

GEAR_SLOTS = [
    (GearSlotCode.WEAPON, "Weapon"),
    (GearSlotCode.OFFHAND, "Offhand"),
    (GearSlotCode.HEAD, "Head"),
    (GearSlotCode.BODY, "Body"),
    (GearSlotCode.HANDS, "Hands"),
    (GearSlotCode.LEGS, "Legs"),
    (GearSlotCode.FEET, "Feet"),
    (GearSlotCode.EARRINGS, "Earrings"),
    (GearSlotCode.NECKLACE, "Necklace"),
    (GearSlotCode.BRACELETS, "Bracelets"),
    (GearSlotCode.RING_1, "Ring 1"),
    (GearSlotCode.RING_2, "Ring 2"),
]

JOBS = {
    role: [(job.abbreviation, job.name) for job in JOB_CATALOG if job.role == role]
    for role in dict.fromkeys(job.role for job in JOB_CATALOG)
}
OFFHAND_CAPABILITY = {job.abbreviation: job.uses_offhand for job in JOB_CATALOG}


@dataclass(frozen=True, slots=True)
class SeedResult:
    inserted_slots: int
    existing_slots: int
    inserted_jobs: int
    existing_jobs: int


def seed_reference_data(session: Session) -> SeedResult:
    """Insert missing slots and combat jobs without changing existing rows."""
    slot_codes = set(session.scalars(select(GearSlot.code)))
    inserted_slots = 0
    for position, (code, display_name) in enumerate(GEAR_SLOTS, 1):
        if code not in slot_codes:
            session.add(GearSlot(code=code, display_name=display_name, sort_order=position))
            inserted_slots += 1

    existing_jobs = {job.abbreviation: job for job in session.scalars(select(Job))}
    inserted_jobs = 0
    for role, jobs in JOBS.items():
        for abbreviation, name in jobs:
            uses_offhand = OFFHAND_CAPABILITY.get(abbreviation, False)
            if abbreviation not in existing_jobs:
                session.add(
                    Job(
                        abbreviation=abbreviation,
                        name=name,
                        role=role,
                        uses_offhand=uses_offhand,
                    )
                )
                inserted_jobs += 1
            else:
                existing_jobs[abbreviation].uses_offhand = uses_offhand
    session.flush()
    bootstrap_default_hierarchies(session)
    return SeedResult(
        inserted_slots,
        len(GEAR_SLOTS) - inserted_slots,
        inserted_jobs,
        sum(len(jobs) for jobs in JOBS.values()) - inserted_jobs,
    )
