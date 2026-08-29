"""Focused relative item-level and eleven-contribution average tests."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    Character,
    CharacterGearSlot,
    CharacterKind,
    DiscordGuild,
    GearClassification,
    GearSlotCode,
    Job,
    Static,
    StaticMember,
)
from app.services.character_gear import initialize_character_gear
from app.services.item_level import (
    AVERAGE_SLOTS,
    calculate_character_item_level,
    calculate_roster_item_levels,
    calculate_slot_item_level,
)
from app.services.seed import seed_reference_data


@pytest.fixture
def item_level_character(session):
    seed_reference_data(session)
    static = Static(
        guild=DiscordGuild(discord_guild_id=9191, name="Relative"),
        name="Relative Static",
        crafted_item_level=710,
    )
    member = StaticMember(static=static, discord_user_id=9192, display_name="Player")
    job = session.scalar(select(Job).where(Job.abbreviation == "WAR"))
    character = Character(
        static_member=member,
        job=job,
        name="Relative Hero",
        world="Fictional",
        kind=CharacterKind.MAIN,
    )
    session.add(character)
    initialize_character_gear(session, character)
    session.commit()
    return (static, character)


@pytest.mark.parametrize(
    ("category", "slot", "expected"),
    [
        (GearClassification.CRAFTED_EX, GearSlotCode.HEAD, 710),
        (GearClassification.TOME, GearSlotCode.HEAD, 720),
        (GearClassification.AUGMENTED_TOME, GearSlotCode.HEAD, 730),
        (GearClassification.SAVAGE, GearSlotCode.HEAD, 730),
        (GearClassification.SAVAGE, GearSlotCode.WEAPON, 735),
        (GearClassification.SAVAGE, GearSlotCode.OFFHAND, 735),
        (GearClassification.GARBAGE, GearSlotCode.HEAD, None),
    ],
)
def test_relative_mapping(category, slot, expected):
    assert calculate_slot_item_level(710, category, slot) == expected


def test_all_crafted_is_average_710(item_level_character, session):
    _, character = item_level_character
    result = calculate_character_item_level(session, character.id)
    assert result.is_valid
    assert result.exact_average == Decimal(710)
    assert result.average_item_level == 710
    assert len(AVERAGE_SLOTS) + 1 == 11


def test_one_tome_slot_floors_only_final_average(item_level_character, session):
    _, character = item_level_character
    head = next(row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.HEAD)
    head.current_classification = GearClassification.TOME
    result = calculate_character_item_level(session, character.id)
    assert result.exact_average == Decimal(7820) / Decimal(11)
    assert result.average_item_level == 710


def test_savage_weapon_uses_plus_25(item_level_character, session):
    _, character = item_level_character
    weapon = next(row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.WEAPON)
    weapon.current_classification = GearClassification.SAVAGE
    result = calculate_character_item_level(session, character.id)
    assert result.weapon_contribution == Decimal(735)


def test_configured_future_offhand_job_preserves_fraction(item_level_character, session):
    static, character = item_level_character
    character.job = Job(abbreviation="EVC", name="Evercold", role="Tank", uses_offhand=True)
    offhand = next(
        row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.OFFHAND
    )
    offhand.current_classification = GearClassification.TOME
    result = calculate_character_item_level(session, character.id)
    assert result.uses_offhand
    assert result.weapon_contribution == Decimal(715)
    assert result.exact_average == Decimal(7815) / Decimal(11)
    assert result.average_item_level == 710
    assert result.static_id == static.id


def test_garbage_offhand_invalidates_capable_job(item_level_character, session):
    _, character = item_level_character
    character.job = Job(abbreviation="EVC", name="Evercold", role="Tank", uses_offhand=True)
    offhand = next(
        row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.OFFHAND
    )
    offhand.current_classification = GearClassification.GARBAGE
    result = calculate_character_item_level(session, character.id)
    assert result.average_item_level is None
    assert result.garbage_slots == (GearSlotCode.OFFHAND,)


@pytest.mark.parametrize("slot_code", [GearSlotCode.BODY, GearSlotCode.WEAPON])
def test_garbage_invalidates_without_partial_average(item_level_character, session, slot_code):
    _, character = item_level_character
    row = next(row for row in character.gear_slots if row.gear_slot.code is slot_code)
    row.current_classification = GearClassification.GARBAGE
    result = calculate_character_item_level(session, character.id)
    assert not result.is_valid
    assert result.average_item_level is None
    assert result.exact_average is None
    assert result.garbage_slots == (slot_code,)
    assert "Gear replacement required immediately" in result.warnings[0]


def test_missing_baseline_warns_and_dynamic_change_recalculates(item_level_character, session):
    static, character = item_level_character
    static.crafted_item_level = None
    missing = calculate_character_item_level(session, character.id)
    assert missing.average_item_level is None
    assert missing.warnings == ("Static crafted item level is not configured.",)
    static.crafted_item_level = 720
    assert calculate_character_item_level(session, character.id).average_item_level == 720


def test_non_offhand_job_requires_explicit_na_state(item_level_character, session):
    _, character = item_level_character
    offhand = next(
        row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.OFFHAND
    )
    offhand.current_classification = GearClassification.CRAFTED_EX
    result = calculate_character_item_level(session, character.id)
    assert result.average_item_level is None
    assert result.missing_or_invalid_slots == (GearSlotCode.OFFHAND,)


def test_roster_calculation_returns_all_characters(item_level_character, session):
    static, character = item_level_character
    results = calculate_roster_item_levels(session, static.id)
    assert tuple(results) == (character.id,)
    assert results[character.id].average_item_level == 710
    assert not hasattr(CharacterGearSlot, "calculated_item_level")
