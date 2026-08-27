"""Domain model integration tests."""

from datetime import date

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    BisSet,
    BisSetItem,
    Character,
    CharacterGearSlot,
    CharacterKind,
    ClearMode,
    DiscordGuild,
    GearClassification,
    GearSlot,
    GearSlotCode,
    InventoryItem,
    Item,
    Job,
    RaidTier,
    SplitGroup,
    SplitParticipant,
    SplitWeek,
    Static,
    StaticMember,
)


def make_character(session: Session, *, suffix: str = "") -> Character:
    guild = DiscordGuild(discord_guild_id=1000 + len(suffix), name=f"Guild{suffix}")
    static = Static(name=f"Static{suffix}", guild=guild)
    member = StaticMember(
        discord_user_id=2000 + len(suffix), display_name=f"Player{suffix}", static=static
    )
    job = Job(abbreviation=f"J{suffix or '0'}", name=f"Job{suffix}", role="Test")
    character = Character(
        name=f"Character{suffix}",
        world=f"World{suffix}",
        kind=CharacterKind.MAIN,
        static_member=member,
        job=job,
    )
    session.add(character)
    session.flush()
    return character


def test_tables_can_be_created(engine) -> None:
    actual_tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= actual_tables
    assert len(actual_tables) == 39


def test_current_gear_has_no_tier_while_tier_owned_configuration_remains(engine) -> None:
    inspector = inspect(engine)
    assert "current_raid_tier_id" not in {
        column["name"] for column in inspector.get_columns("character_gear_slots")
    }
    for table in ("bis_sets", "raid_floors", "loot_types", "split_weeks"):
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert "raid_tier_id" in columns
        assert columns["raid_tier_id"]["nullable"] is False


def test_relationships_work(session: Session) -> None:
    character = make_character(session)
    session.commit()
    session.expire_all()

    loaded = session.scalar(select(Character).where(Character.id == character.id))

    assert loaded is not None
    assert loaded.static_member.static.guild.name == "Guild"
    assert loaded.static_member.characters == [loaded]
    assert loaded.job.name == "Job"


def test_ring_one_and_ring_two_remain_distinct(session: Session) -> None:
    ring_one = GearSlot(code=GearSlotCode.RING_1, display_name="Ring 1", sort_order=11)
    ring_two = GearSlot(code=GearSlotCode.RING_2, display_name="Ring 2", sort_order=12)
    session.add_all([ring_one, ring_two])
    session.commit()

    slots = session.scalars(select(GearSlot).order_by(GearSlot.sort_order)).all()
    assert [slot.code for slot in slots] == [GearSlotCode.RING_1, GearSlotCode.RING_2]
    assert slots[0].id != slots[1].id


def test_job_can_have_multiple_bis_sets_for_same_tier(session: Session) -> None:
    job = Job(abbreviation="BLM", name="Black Mage", role="Magical Ranged DPS")
    tier = RaidTier(code="TEST_TIER", name="Test Tier")
    job.bis_sets.extend(
        [
            BisSet(name="2.42 GCD", gcd_label="2.42", raid_tier=tier),
            BisSet(name="2.48 GCD", gcd_label="2.48", raid_tier=tier),
        ]
    )
    session.add(job)
    session.commit()

    assert {bis_set.name for bis_set in job.bis_sets} == {"2.42 GCD", "2.48 GCD"}
    assert all(bis_set.raid_tier is tier for bis_set in job.bis_sets)


def test_character_has_separate_current_gear_and_inventory(session: Session) -> None:
    character = make_character(session)
    slot = GearSlot(code=GearSlotCode.FEET, display_name="Feet", sort_order=7)
    carried = Item(name="Spare Boots")
    character.gear_slots.append(
        CharacterGearSlot(gear_slot=slot, current_classification=GearClassification.CRAFTED)
    )
    character.inventory_items.append(InventoryItem(item=carried, quantity=2))
    session.commit()

    assert character.gear_slots[0].current_classification is GearClassification.CRAFTED
    assert character.inventory_items[0].item.name == "Spare Boots"
    assert character.inventory_items[0].quantity == 2


def test_duplicate_bis_slots_are_rejected(session: Session) -> None:
    job = Job(abbreviation="RDM", name="Red Mage", role="Magical Ranged DPS")
    tier = RaidTier(code="DUP_TIER", name="Duplicate Test Tier")
    slot = GearSlot(code=GearSlotCode.BODY, display_name="Body", sort_order=4)
    bis_set = BisSet(name="Default", job=job, raid_tier=tier)
    bis_set.items.extend(
        [
            BisSetItem(gear_slot=slot, classification=GearClassification.SAVAGE),
            BisSetItem(gear_slot=slot, classification=GearClassification.CRAFTED),
        ]
    )
    session.add(bis_set)

    with pytest.raises(IntegrityError):
        session.commit()


def test_negative_inventory_quantities_are_rejected(session: Session) -> None:
    character = make_character(session)
    session.add(InventoryItem(character=character, item=Item(name="Invalid Item"), quantity=-1))

    with pytest.raises(IntegrityError):
        session.commit()


def test_same_character_cannot_repeat_within_one_split_group(session: Session) -> None:
    character = make_character(session)
    tier = RaidTier(code="WEEK_TIER", name="Week Tier")
    split_week = SplitWeek(
        static=character.static_member.static,
        raid_tier=tier,
        week_start=date(2026, 8, 25),
        clear_mode=ClearMode.SPLIT,
    )
    group_one = SplitGroup(split_week=split_week, group_number=1)
    session.add_all([split_week, group_one])
    session.flush()
    session.add_all(
        [
            SplitParticipant(split_week=split_week, split_group=group_one, character=character),
            SplitParticipant(split_week=split_week, split_group=group_one, character=character),
        ]
    )

    with pytest.raises(IntegrityError):
        session.commit()
