"""Reference seed and transactional JSON import tests."""

from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BisSet, GearSlot, Item, Job, LootType, RaidTier
from app.services import (
    ImportValidationError,
    import_bis_sets,
    import_raid_tier,
    seed_reference_data,
)

ROOT = Path(__file__).parents[1]
TIER_FIXTURE = ROOT / "sample_data" / "fictional_raid_tier.json"
BIS_FIXTURE = ROOT / "sample_data" / "fictional_bis_sets.json"


def test_idempotent_reference_seeding(session: Session) -> None:
    seed_reference_data(session)
    session.commit()
    seed_reference_data(session)
    session.commit()
    assert session.scalar(select(func.count()).select_from(GearSlot)) == 12
    assert session.scalar(select(func.count()).select_from(Job)) == 21
    assert session.scalar(select(Job.role).where(Job.abbreviation == "GNB")) == "Tank"
    assert session.scalar(select(Job.role).where(Job.abbreviation == "PCT")) == "Magical Ranged DPS"


def test_valid_dry_runs_do_not_write(session: Session) -> None:
    seed_reference_data(session)
    session.commit()
    assert import_raid_tier(session, TIER_FIXTURE, dry_run=True) is None
    assert session.scalar(select(func.count()).select_from(RaidTier)) == 0

    import_raid_tier(session, TIER_FIXTURE)
    session.commit()
    assert import_bis_sets(session, BIS_FIXTURE, dry_run=True) == []
    assert session.scalar(select(func.count()).select_from(BisSet)) == 0


def test_valid_import_format(session: Session) -> None:
    seed_reference_data(session)
    import_raid_tier(session, TIER_FIXTURE)
    session.commit()
    imported = import_bis_sets(session, BIS_FIXTURE)
    session.commit()
    assert imported[0].gear_set_url == "https://example.invalid/fictional-set"
    assert len(imported[0].items) == 3
    assert imported[0].items[1].tome_cost == 825
    coffer = session.scalar(select(LootType).where(LootType.code == "HEAD_COFFER"))
    assert coffer.item.name == "Fictional Head Coffer"


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("job", "NOPE", "set Invalid.job"),
        ("slot", "WAIST", "set Invalid, slot WAIST.slot"),
        ("classification", "UNKNOWN", "classification"),
        ("tome_cost", -1, "tome_cost"),
    ],
)
def test_invalid_bis_dry_runs_identify_context(
    session: Session, field: str, value: object, expected: str
) -> None:
    seed_reference_data(session)
    import_raid_tier(session, TIER_FIXTURE)
    session.commit()
    item = {
        "slot": "HEAD",
        "desired_item": "Invalid Item",
        "classification": "TOME",
    }
    row = {"tier_code": "FICTIONAL_ARC", "job": "PLD", "name": "Invalid", "items": [item]}
    if field == "job":
        row[field] = value
    else:
        item[field] = value
    with pytest.raises(ImportValidationError, match=expected):
        import_bis_sets(session, {"sets": [row]}, dry_run=True)
    assert session.scalar(select(func.count()).select_from(BisSet)) == 0


def test_duplicate_slots_and_invalid_augmented_tome_rejected(session: Session) -> None:
    seed_reference_data(session)
    import_raid_tier(session, TIER_FIXTURE)
    session.commit()
    item = {"slot": "HEAD", "desired_item": "Hat", "classification": "AUGMENTED_TOME"}
    data = {
        "sets": [
            {
                "tier_code": "FICTIONAL_ARC",
                "job": "PLD",
                "name": "Broken",
                "items": [item, deepcopy(item)],
            }
        ]
    }
    with pytest.raises(ImportValidationError) as error:
        import_bis_sets(session, data, dry_run=True)
    assert "duplicate slot" in str(error.value)
    assert "base_tome_item" in str(error.value)


def test_contradictory_not_applicable_import_rejected(session: Session) -> None:
    seed_reference_data(session)
    import_raid_tier(session, TIER_FIXTURE)
    session.commit()
    data = {
        "sets": [
            {
                "tier_code": "FICTIONAL_ARC",
                "job": "PLD",
                "name": "Contradictory",
                "items": [
                    {
                        "slot": "OFFHAND",
                        "classification": "NOT_APPLICABLE",
                        "desired_item": "This must not be present",
                    }
                ],
            }
        ]
    }
    with pytest.raises(ImportValidationError, match="contradictory NOT_APPLICABLE"):
        import_bis_sets(session, data, dry_run=True)


@pytest.mark.parametrize(
    ("job", "classification", "desired", "message"),
    [
        ("PLD", "NOT_APPLICABLE", None, "PLD OFFHAND must define an applicable"),
        ("WAR", "CRAFTED", "Fictional Axe Offhand", "WAR OFFHAND must be NOT_APPLICABLE"),
    ],
)
def test_ffxiv_offhand_rule_is_enforced_during_import(
    session: Session, job: str, classification: str, desired: str | None, message: str
) -> None:
    seed_reference_data(session)
    import_raid_tier(session, TIER_FIXTURE)
    session.commit()
    item = {"slot": "OFFHAND", "classification": classification}
    if desired:
        item["desired_item"] = desired
    data = {
        "sets": [{"tier_code": "FICTIONAL_ARC", "job": job, "name": "Bad Offhand", "items": [item]}]
    }

    with pytest.raises(ImportValidationError, match=message):
        import_bis_sets(session, data, dry_run=True)


def test_invalid_tier_dry_run_has_field_context(session: Session) -> None:
    invalid = {
        "code": "BROKEN",
        "name": "Broken",
        "floors": [{"number": 1, "name": "One"}],
        "loot_types": [],
        "augmentation_material_types": [],
        "floor_loot_rules": [{"floor": 9, "loot_type": "NOPE", "expected_quantity": -1}],
    }
    with pytest.raises(ImportValidationError) as error:
        import_raid_tier(session, invalid, dry_run=True)
    message = str(error.value)
    assert "floor_loot_rules[0].floor" in message
    assert "floor_loot_rules[0].loot_type" in message
    assert session.scalar(select(func.count()).select_from(RaidTier)) == 0


def test_unreferenced_bis_reimport_updates_without_duplicates(session: Session) -> None:
    seed_reference_data(session)
    import_raid_tier(session, TIER_FIXTURE)
    session.commit()
    existing = BisSet(
        raid_tier=session.scalar(select(RaidTier).where(RaidTier.code == "FICTIONAL_ARC")),
        job=session.scalar(select(Job).where(Job.abbreviation == "PLD")),
        name="Fictional PLD Sample",
    )
    session.add(existing)
    session.commit()
    result = import_bis_sets(session, BIS_FIXTURE)
    assert result.counts.updated == 1
    assert session.scalar(select(Item).where(Item.name == "Fictional Savage Helm")) is not None
    assert session.scalar(select(func.count()).select_from(BisSet)) == 1
