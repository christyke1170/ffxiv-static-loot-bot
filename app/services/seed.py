from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GearSlot, GearSlotCode, Job

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
    "Tank": [("PLD", "Paladin"), ("WAR", "Warrior"), ("DRK", "Dark Knight"), ("GNB", "Gunbreaker")],
    "Healer": [("WHM", "White Mage"), ("SCH", "Scholar"), ("AST", "Astrologian"), ("SGE", "Sage")],
    "Melee DPS": [
        ("MNK", "Monk"),
        ("DRG", "Dragoon"),
        ("NIN", "Ninja"),
        ("SAM", "Samurai"),
        ("RPR", "Reaper"),
        ("VPR", "Viper"),
    ],
    "Physical Ranged DPS": [("BRD", "Bard"), ("MCH", "Machinist"), ("DNC", "Dancer")],
    "Magical Ranged DPS": [
        ("BLM", "Black Mage"),
        ("SMN", "Summoner"),
        ("RDM", "Red Mage"),
        ("PCT", "Pictomancer"),
    ],
}


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

    job_codes = set(session.scalars(select(Job.abbreviation)))
    inserted_jobs = 0
    for role, jobs in JOBS.items():
        for abbreviation, name in jobs:
            if abbreviation not in job_codes:
                session.add(Job(abbreviation=abbreviation, name=name, role=role))
                inserted_jobs += 1
    session.flush()
    return SeedResult(
        inserted_slots,
        len(GEAR_SLOTS) - inserted_slots,
        inserted_jobs,
        sum(len(jobs) for jobs in JOBS.values()) - inserted_jobs,
    )
