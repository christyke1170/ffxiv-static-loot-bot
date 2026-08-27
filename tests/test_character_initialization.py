"""Focused shared new-character gear initialization and Offhand reconciliation tests."""

import pytest
from sqlalchemy import func, select

from app.models import (
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
from app.services.seed import seed_reference_data
from bot.services.admin import add_character, edit_character


@pytest.fixture
def member_without_tier(session):
    seed_reference_data(session)
    static = Static(
        guild=DiscordGuild(discord_guild_id=88001, name="Initialization"),
        name="No Tier Static",
    )
    member = StaticMember(static=static, discord_user_id=88002, display_name="Initializer")
    session.add(member)
    session.flush()
    return static, member


@pytest.mark.parametrize("kind", [CharacterKind.MAIN, CharacterKind.ALT])
def test_new_character_starts_with_all_category_state(session, member_without_tier, kind):
    _, member = member_without_tier
    character = add_character(
        session,
        member,
        f"New {kind.value}",
        "Fictional",
        kind,
        "WAR",
    )
    rows = {row.gear_slot.code: row.current_classification for row in character.gear_slots}
    assert len(rows) == 12
    assert rows[GearSlotCode.OFFHAND] is GearClassification.NOT_APPLICABLE
    assert all(
        category is GearClassification.CRAFTED_EX
        for code, category in rows.items()
        if code is not GearSlotCode.OFFHAND
    )
    assert character.static_member.static.active_raid_tier_id is None


def test_initializer_never_overwrites_existing_state(session, member_without_tier):
    _, member = member_without_tier
    character = add_character(session, member, "Existing", "Fictional", CharacterKind.MAIN, "WAR")
    head = next(row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.HEAD)
    head.current_classification = GearClassification.SAVAGE
    assert initialize_character_gear(session, character) == ()
    assert head.current_classification is GearClassification.SAVAGE
    assert (
        session.scalar(
            select(func.count())
            .select_from(CharacterGearSlot)
            .where(CharacterGearSlot.character_id == character.id)
        )
        == 12
    )


def test_future_configured_offhand_job_needs_no_name_check(session, member_without_tier):
    _, member = member_without_tier
    session.add(Job(abbreviation="EVC", name="Evercold", role="Tank", uses_offhand=True))
    session.flush()
    character = add_character(session, member, "Future", "Fictional", CharacterKind.MAIN, "EVC")
    offhand = next(
        row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.OFFHAND
    )
    assert offhand.current_classification is GearClassification.CRAFTED_EX


def test_job_changes_reconcile_only_offhand(session, member_without_tier):
    static, member = member_without_tier
    session.add(Job(abbreviation="EVC", name="Evercold", role="Tank", uses_offhand=True))
    session.flush()
    character = add_character(session, member, "Switcher", "Fictional", CharacterKind.MAIN, "WAR")
    head = next(row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.HEAD)
    offhand = next(
        row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.OFFHAND
    )
    head.current_classification = GearClassification.SAVAGE

    edit_character(session, static, character, 88002, new_job="EVC")
    assert offhand.current_classification is GearClassification.CRAFTED_EX
    assert head.current_classification is GearClassification.SAVAGE

    offhand.current_classification = GearClassification.SAVAGE
    edit_character(session, static, character, 88002, new_job="WAR")
    assert offhand.current_classification is GearClassification.NOT_APPLICABLE
    assert head.current_classification is GearClassification.SAVAGE


def test_seeded_offhand_configuration(session):
    seed_reference_data(session)
    jobs = {job.abbreviation: job.uses_offhand for job in session.scalars(select(Job))}
    assert jobs["PLD"] is True
    assert all(not enabled for code, enabled in jobs.items() if code != "PLD")
