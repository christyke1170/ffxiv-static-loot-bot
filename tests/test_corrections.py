"""Safe correction, lifecycle, selection, and immutable-history regression tests."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    BisSet,
    Character,
    CharacterBisSelection,
    CharacterGearSlot,
    CharacterKind,
    DiscordGuild,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Job,
    JobHierarchy,
    JobHierarchyEntry,
    RaidTier,
    ReclearGroup,
    ReclearParticipant,
    ReclearWeek,
    Static,
    StaticMember,
    WeeklyHierarchySnapshotEntry,
)
from app.services import import_bis_sets, import_raid_tier, seed_reference_data
from app.services.gear import set_inventory
from app.services.weeks import snapshot_hierarchy
from bot.services.admin import (
    clear_bis,
    clear_tier,
    deactivate_static,
    edit_character,
    edit_member,
    edit_static,
    reactivate_member,
    reactivate_static,
    select_bis,
    select_tier,
    set_character_active,
)
from tests.bot.helpers import BIS_DATA, TIER_DATA


@pytest.fixture
def correction_state(session):
    seed_reference_data(session)
    guild = DiscordGuild(discord_guild_id=700, name="Correction Guild")
    static = Static(guild=guild, name="Original")
    member = StaticMember(static=static, discord_user_id=10, display_name="Old Name")
    pld = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    character = Character(
        static_member=member,
        job=pld,
        name="Old Character",
        world="Old World",
        kind=CharacterKind.MAIN,
    )
    session.add(static)
    session.commit()
    return static, member, character


def test_static_rename_deactivate_reactivate_and_duplicate(session, correction_state):
    static, _, _ = correction_state
    session.add(Static(guild=static.guild, name="Taken"))
    session.flush()
    edit_static(session, static, "Renamed", 99)
    row_id = static.id
    assert static.name == "Renamed"
    with pytest.raises(ValueError, match="already exists"):
        edit_static(session, static, "Taken", 99)
    deactivate_static(session, static, 99)
    assert not static.active and static.id == row_id
    reactivate_static(session, static, 99)
    assert static.active and static.id == row_id


def test_static_deactivation_blocked_by_open_week(session, correction_state):
    static, _, _ = correction_state
    tier = RaidTier(code="OPEN", name="Open")
    session.add(
        ReclearWeek(
            static=static,
            raid_tier=tier,
            week_start=date(2026, 8, 25),
            clear_mode="REGULAR",
        )
    )
    session.flush()
    with pytest.raises(ValueError, match="unfinished reclear"):
        deactivate_static(session, static, 99)
    assert static.active


def test_member_edit_and_reactivation_preserve_characters(session, correction_state):
    static, member, character = correction_state
    character_id = character.id
    edit_member(session, static, member.discord_user_id, "Correct Name", 99)
    member.active = False
    reactivate_member(session, static, member.discord_user_id, 99)
    assert member.display_name == "Correct Name" and member.active
    assert member.characters[0].id == character_id


def test_character_multifield_edit_preserves_row_and_relationships(session, correction_state):
    static, _, character = correction_state
    slot = session.scalar(select(GearSlot).where(GearSlot.code == GearSlotCode.HEAD))
    gear = CharacterGearSlot(
        character=character,
        gear_slot=slot,
        current_classification=GearClassification.GARBAGE,
    )
    session.add(gear)
    session.flush()
    row_id, gear_id = character.id, gear.id
    edited, cleared = edit_character(
        session,
        static,
        character,
        99,
        new_name="New Character",
        new_world="New World",
        new_kind=CharacterKind.ALT,
        new_job="WAR",
    )
    assert cleared == 0
    assert (edited.id, edited.name, edited.world, edited.kind, edited.job.abbreviation) == (
        row_id,
        "New Character",
        "New World",
        CharacterKind.ALT,
        "WAR",
    )
    assert (
        next(row for row in edited.gear_slots if row.gear_slot.code is GearSlotCode.HEAD).id
        == gear_id
    )


def test_character_edit_validation_and_atomic_rollback(session, correction_state):
    static, member, character = correction_state
    other = Character(
        static_member=member,
        job=character.job,
        name="Taken",
        world="World",
        kind=CharacterKind.ALT,
    )
    session.add(other)
    session.commit()
    with pytest.raises(ValueError, match="at least one"):
        edit_character(session, static, character, 99)
    with pytest.raises(ValueError, match="name/world"):
        edit_character(
            session, static, character, 99, new_name="Taken", new_world="World", new_job="WAR"
        )
    session.rollback()
    assert session.get(Character, character.id).job.abbreviation == "PLD"


def test_character_job_change_requires_explicit_incompatible_bis_clear(session, correction_state):
    static, _, character = correction_state
    tier = RaidTier(code="BIS", name="BiS")
    bis_set = BisSet(job=character.job, raid_tier=tier, name="PLD Set")
    selection = CharacterBisSelection(character=character, raid_tier=tier, bis_set=bis_set)
    session.add(selection)
    session.commit()
    with pytest.raises(ValueError, match="clear_incompatible_bis"):
        edit_character(session, static, character, 99, new_job="WAR")
    edited, cleared = edit_character(
        session, static, character, 99, new_job="WAR", clear_incompatible_bis=True
    )
    session.flush()
    assert edited.job.abbreviation == "WAR" and cleared == 1
    assert session.scalar(select(CharacterBisSelection)) is None


def test_character_deactivation_blocked_by_open_workflow(session, correction_state):
    static, _, character = correction_state
    tier = RaidTier(code="FLOW", name="Flow")
    week = ReclearWeek(
        static=static,
        raid_tier=tier,
        week_start=date(2026, 8, 25),
        clear_mode="REGULAR",
    )
    group = ReclearGroup(reclear_week=week, group_number=1)
    session.add(ReclearParticipant(reclear_week=week, group=group, character=character))
    session.flush()
    with pytest.raises(ValueError, match="open reclear"):
        set_character_active(session, static, character, False, 99)
    assert character.active


def test_tier_and_bis_reselection_and_clear_are_idempotent(session, correction_state):
    static, _, character = correction_state
    first = RaidTier(code="FIRST", name="First")
    second = RaidTier(code="SECOND", name="Second")
    session.add_all([first, second])
    session.flush()
    assert select_tier(static, first).changed
    assert not select_tier(static, first).changed
    assert select_tier(static, second).old is first
    bis_one = BisSet(job=character.job, raid_tier=second, name="One")
    bis_two = BisSet(job=character.job, raid_tier=second, name="Two")
    session.add_all([bis_one, bis_two])
    session.flush()
    assert select_bis(session, character, second, bis_one).changed
    assert select_bis(session, character, second, bis_two).old is bis_one
    assert clear_bis(session, static, character, second).changed
    assert not clear_bis(session, static, character, second).changed
    assert clear_tier(session, static).changed


def test_week_tier_and_hierarchy_snapshots_survive_current_selection_changes(
    session, correction_state
):
    static, _, _ = correction_state
    first = RaidTier(code="SNAP1", name="Snapshot One")
    second = RaidTier(code="SNAP2", name="Snapshot Two")
    job = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    hierarchy = JobHierarchy(static=static, version=1, active_marker=True)
    hierarchy.entries.append(JobHierarchyEntry(job=job, position=1))
    week = ReclearWeek(
        static=static,
        raid_tier=first,
        week_start=date(2026, 8, 18),
        clear_mode="REGULAR",
        workflow_state="CLOSED",
    )
    snapshot_hierarchy(week, hierarchy)
    session.add_all([second, week])
    session.flush()
    select_tier(static, second)
    hierarchy.active_marker = None
    session.flush()
    assert week.raid_tier is first
    assert [(row.job_abbreviation, row.position) for row in week.hierarchy_snapshot] == [("PLD", 1)]
    assert session.scalar(select(func.count()).select_from(WeeklyHierarchySnapshotEntry)) == 1


def test_safe_and_unsafe_definition_reimports(session):
    seed_reference_data(session)
    tier = import_raid_tier(session, TIER_DATA)
    unchanged = import_raid_tier(session, TIER_DATA)
    assert unchanged.import_counts.unchanged == 1
    changed = dict(TIER_DATA)
    changed["name"] = "Corrected Tier"
    updated = import_raid_tier(session, changed)
    assert updated.id == tier.id and updated.import_counts.updated == 1
    bis_rows = import_bis_sets(session, BIS_DATA)
    assert bis_rows.counts.inserted == 1
    character_job = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    guild = DiscordGuild(discord_guild_id=701, name="Imports")
    static = Static(guild=guild, name="Imports", active_raid_tier=tier)
    member = StaticMember(static=static, discord_user_id=1, display_name="Member")
    character = Character(
        static_member=member,
        job=character_job,
        name="Import Character",
        world="World",
        kind=CharacterKind.MAIN,
    )
    session.add(CharacterBisSelection(character=character, raid_tier=tier, bis_set=bis_rows[0]))
    session.flush()
    changed_bis = {"sets": [dict(BIS_DATA["sets"][0], description="Changed")]}
    rejected = import_bis_sets(session, changed_bis)
    assert rejected.counts.rejected == 1
    assert bis_rows[0].description != "Changed"


def test_resource_correction_is_idempotent_and_audited(session, correction_state):
    static, _, character = correction_state
    slot = session.scalar(select(GearSlot).where(GearSlot.code == GearSlotCode.HEAD))
    first = set_inventory(session, static, character, slot, GearClassification.SAVAGE, 2, 99)
    second = set_inventory(session, static, character, slot, GearClassification.SAVAGE, 2, 99)
    assert first.id == second.id
    assert session.scalar(select(func.count()).select_from(AuditLog)) == 2
    with pytest.raises(ValueError, match="negative"):
        set_inventory(session, static, character, slot, GearClassification.SAVAGE, -1, 99)
