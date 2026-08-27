from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    Character,
    CharacterBisSelection,
    CharacterKind,
    ClearMode,
    DiscordGuild,
    GearClassification,
    GearSlotCode,
    JobHierarchy,
    LootAssignmentState,
    LootCategory,
    RaidTier,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    StaticMember,
    UserStaticPreference,
)
from app.schemas.board import DisplayStatus
from app.schemas.needs import BookAvailability, NeedStatus
from app.services import (
    close_reclear_week,
    confirm_augmentation_applied,
    confirm_coffer_redemption,
    confirm_loot_received,
    create_reclear_week,
    generate_weekly_loot_plan,
    mark_assignment_disposition,
    mark_reclear_floors_complete,
)
from app.services.board import build_static_gear_board
from app.services.needs import calculate_character_needs
from app.services.weeks import ResetPeriodPolicy
from bot.commands.setup import Setup
from bot.services.demo import DEMO_STATIC_NAME, synthetic_demo_user_ids
from tests.bot.conftest import BOT_ADMIN_ROLE
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static

SPLIT_WEEK = ResetPeriodPolicy().week_start(date.today())
REGULAR_WEEK = SPLIT_WEEK - timedelta(days=7)


async def create_demo(bot, interaction_factory, *, guild_id=100, user_id=200):
    interaction = interaction_factory(guild_id=guild_id, user_id=user_id, roles=(BOT_ADMIN_ROLE,))
    await invoke_registered(Setup(bot), "demo", interaction, handle_errors=False)
    return interaction


async def refresh_demo(bot, interaction_factory, *, guild_id=100, user_id=200):
    interaction = interaction_factory(guild_id=guild_id, user_id=user_id, roles=(BOT_ADMIN_ROLE,))
    await invoke_registered(Setup(bot), "demo-refresh", interaction, handle_errors=False)
    return interaction


def demo_static(session, guild_id=100):
    return session.scalar(
        select(Static)
        .join(DiscordGuild)
        .where(
            DiscordGuild.discord_guild_id == guild_id,
            Static.name == DEMO_STATIC_NAME,
        )
    )


async def test_demo_requires_bot_admin_and_writes_nothing(bot, interaction_factory):
    interaction = interaction_factory(roles=())

    await invoke_registered(Setup(bot), "demo", interaction)

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Static)) == 0
    assert interaction.messages[0]["content"] == "You do not have permission to use this command."


async def test_demo_refresh_requires_bot_admin(bot, interaction_factory):
    await create_demo(bot, interaction_factory)
    interaction = interaction_factory(roles=())

    await invoke_registered(Setup(bot), "demo-refresh", interaction)

    assert interaction.messages[0]["content"] == "You do not have permission to use this command."


async def test_demo_creates_complete_selected_isolated_state(bot, interaction_factory):
    real_id = arrange_static(bot, name="Real Static")

    interaction = await create_demo(bot, interaction_factory)

    with bot.session_factory() as session:
        static = demo_static(session)
        real = session.get(Static, real_id)
        members = [row for row in static.members if row.active]
        characters = [row for member in members for row in member.characters if row.active]
        assert real.name == "Real Static" and real.members == [] and real.active_raid_tier is None
        assert len(members) == 8
        assert {row.display_name for row in members} == {f"Player {index}" for index in range(1, 9)}
        assert len(characters) == 16
        assert {row.name for row in characters} == {
            f"Player {index} {kind}" for index in range(1, 9) for kind in ("Main", "Alt")
        }
        assert sum(row.kind is CharacterKind.MAIN for row in characters) == 8
        assert sum(row.kind is CharacterKind.ALT for row in characters) == 8
        assert any(row.discord_user_id == 200 for row in members)
        assert {row.discord_user_id for row in members if row.discord_user_id != 200} == set(
            synthetic_demo_user_ids(100)
        )
        assert all(row.discord_user_id < 0 for row in members if row.discord_user_id != 200)
        assert static.active_raid_tier.name.startswith("Fictional Demo")
        assert len(static.active_raid_tier.floors) == 4
        assert len(static.active_raid_tier.loot_types) == 4
        assert len(static.active_raid_tier.augmentation_material_types) == 2
        assert session.scalar(select(func.count()).select_from(CharacterBisSelection)) == 16
        assert all(
            len(selection.bis_set.items) == 12
            for character in characters
            for selection in character.bis_selections
        )
        hierarchy = session.scalar(
            select(JobHierarchy).where(
                JobHierarchy.static_id == static.id,
                JobHierarchy.active_marker.is_(True),
            )
        )
        assert hierarchy.version == 1 and len(hierarchy.entries) == 8
        assert {row.job_id for row in hierarchy.entries} == {
            character.job_id for character in characters if character.kind is CharacterKind.MAIN
        }
        assert session.scalar(select(func.count()).select_from(ReclearWeek)) == 0
        selections = list(session.scalars(select(CharacterBisSelection)))
        for selection in selections:
            offhand = next(
                item
                for item in selection.bis_set.items
                if item.gear_slot.code is GearSlotCode.OFFHAND
            )
            if selection.character.job.abbreviation == "PLD":
                assert offhand.classification is not GearClassification.NOT_APPLICABLE
                assert offhand.desired_item is not None
            else:
                assert offhand.classification is GearClassification.NOT_APPLICABLE
        assert build_static_gear_board(session, static.id).players
        statuses = set()
        book_states = set()
        for character in characters:
            needs = calculate_character_needs(session, character.id, static.active_raid_tier_id)
            statuses.update(row.status for row in needs.slot_results)
            book_states.update(row.book_availability for row in needs.slot_results)
        assert {
            NeedStatus.COMPLETE,
            NeedStatus.NEEDS_SAVAGE_DROP,
            NeedStatus.NEEDS_BASE_TOME_ITEM,
            NeedStatus.NEEDS_AUGMENTATION,
            NeedStatus.READY_TO_AUGMENT,
            NeedStatus.OWNED_COFFER_AVAILABLE,
        } <= statuses
        assert BookAvailability.NEEDS_MORE_BOOKS in book_states
    assert "No reclear week was created" in interaction.messages[0]["content"]


async def test_standard_demo_has_representative_current_state_without_unknowns(
    bot, interaction_factory
):
    await create_demo(bot, interaction_factory)

    with bot.session_factory() as session:
        static = demo_static(session)
        board = build_static_gear_board(session, static.id)
        statuses = {slot.display_status for player in board.players for slot in player.slots}
        applicable = [
            slot
            for player in board.players
            for slot in player.slots
            if slot.display_status is not DisplayStatus.NA
        ]
        assert DisplayStatus.NEEDS_REPLACEMENT not in statuses
        assert {
            DisplayStatus.BIS,
            DisplayStatus.TOME_NEEDS_AUGMENT,
            DisplayStatus.CRAFTED_EX,
        } <= statuses
        assert all(slot.current_classification is not None for slot in applicable)
        current_rows = [
            row
            for character in _active_characters(static)
            for row in character.gear_slots
            if row.gear_slot.code is not GearSlotCode.OFFHAND or character.job.abbreviation == "PLD"
        ]
        assert all(
            row.current_classification
            in {
                GearClassification.CRAFTED,
                GearClassification.EX_WEAPON,
                GearClassification.SAVAGE,
                GearClassification.TOME,
                GearClassification.AUGMENTED_TOME,
                GearClassification.GARBAGE,
            }
            for row in current_rows
        )


async def test_demo_offhand_current_state_matches_job_applicability(bot, interaction_factory):
    await create_demo(bot, interaction_factory)

    with bot.session_factory() as session:
        static = demo_static(session)
        for character in _active_characters(static):
            offhands = [
                row for row in character.gear_slots if row.gear_slot.code is GearSlotCode.OFFHAND
            ]
            if character.job.abbreviation == "PLD":
                assert len(offhands) == 1
            else:
                assert offhands == []


async def test_demo_is_atomic_and_repeated_creation_cannot_duplicate(
    bot, interaction_factory, monkeypatch
):
    import bot.services.demo as demo_module

    original = demo_module.import_bis_sets

    def fail_after_static_created(*args, **kwargs):
        raise RuntimeError("injected demo failure")

    monkeypatch.setattr(demo_module, "import_bis_sets", fail_after_static_created)
    interaction = interaction_factory(roles=(BOT_ADMIN_ROLE,))
    with pytest.raises(RuntimeError, match="injected"):
        await invoke_registered(Setup(bot), "demo", interaction, handle_errors=False)
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Static)) == 0
        assert session.scalar(select(func.count()).select_from(RaidTier)) == 0
        assert session.scalar(select(func.count()).select_from(Character)) == 0

    monkeypatch.setattr(demo_module, "import_bis_sets", original)
    await create_demo(bot, interaction_factory)
    repeated = interaction_factory(roles=(BOT_ADMIN_ROLE,))
    await invoke_registered(Setup(bot), "demo", repeated)
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Static)) == 1
        assert session.scalar(select(func.count()).select_from(StaticMember)) == 8
        assert session.scalar(select(func.count()).select_from(Character)) == 16
    assert "already exists" in repeated.messages[0]["content"]


async def test_demo_guilds_are_isolated_and_ids_are_deterministic(bot, interaction_factory):
    assert synthetic_demo_user_ids(100) == synthetic_demo_user_ids(100)
    assert set(synthetic_demo_user_ids(100)).isdisjoint(synthetic_demo_user_ids(101))
    assert all(-(2**63) < value < 0 for value in synthetic_demo_user_ids(9_223_372_036_854_775_000))
    await create_demo(bot, interaction_factory, guild_id=100, user_id=200)
    await create_demo(bot, interaction_factory, guild_id=101, user_id=201)

    with bot.session_factory() as session:
        first = demo_static(session, 100)
        second = demo_static(session, 101)
        assert first.id != second.id
        assert first.guild_id != second.guild_id
        assert first.active_raid_tier_id != second.active_raid_tier_id
        assert {row.discord_user_id for row in first.members}.isdisjoint(
            {row.discord_user_id for row in second.members}
        )


async def test_demo_refresh_repairs_pre_fix_pld_and_is_idempotent(bot, interaction_factory):
    await create_demo(bot, interaction_factory)
    with bot.session_factory() as session:
        static = demo_static(session)
        static_id = static.id
        pld = next(row for row in _active_characters(static) if row.job.abbreviation == "PLD")
        selection = next(
            row for row in pld.bis_selections if row.raid_tier_id == static.active_raid_tier_id
        )
        offhand = next(
            row for row in selection.bis_set.items if row.gear_slot.code is GearSlotCode.OFFHAND
        )
        offhand.classification = GearClassification.CRAFTED
        offhand.desired_item.name = "Fictional Demo G100 PLD Fictional Crafted Shield"
        offhand.raid_floor = None
        offhand.loot_type = None
        offhand.book_cost = None
        current_offhand = next(
            row for row in pld.gear_slots if row.gear_slot.code is GearSlotCode.OFFHAND
        )
        session.delete(current_offhand)
        session.delete(selection)
        session.commit()

    refreshed = await refresh_demo(bot, interaction_factory)
    with bot.session_factory() as session:
        static = demo_static(session)
        assert static.id == static_id
        characters = _active_characters(static)
        pld = next(row for row in characters if row.job.abbreviation == "PLD")
        pld_needs = calculate_character_needs(session, pld.id, static.active_raid_tier_id)
        assert pld_needs.total_applicable_slot_count == 12
        assert all(
            calculate_character_needs(
                session, row.id, static.active_raid_tier_id
            ).total_applicable_slot_count
            == 11
            for row in characters
            if row.job.abbreviation != "PLD"
        )
        selection = next(
            row for row in pld.bis_selections if row.raid_tier_id == static.active_raid_tier_id
        )
        offhand = next(
            row for row in selection.bis_set.items if row.gear_slot.code is GearSlotCode.OFFHAND
        )
        assert offhand.desired_item.name == "Fictional Demo G100 PLD Savage Shield"
        assert offhand.loot_type.code == "WEAPON_COFFER" and offhand.raid_floor.floor_number == 4
        assert any(row.gear_slot.code is GearSlotCode.OFFHAND for row in pld.gear_slots)
    assert "updated" in refreshed.messages[0]["content"]

    repeated = await refresh_demo(bot, interaction_factory)
    assert "created 0, updated 0" in repeated.messages[0]["content"]


async def test_demo_refresh_rejects_real_static(bot, interaction_factory):
    real_id = arrange_static(bot, name="Real Static")
    interaction = interaction_factory(roles=(BOT_ADMIN_ROLE,))
    with bot.session_factory() as session:
        real = session.get(Static, real_id)
        guild_row = real.guild
        preference = session.scalar(
            select(UserStaticPreference).where(
                UserStaticPreference.guild_id == guild_row.id,
                UserStaticPreference.discord_user_id == interaction.user.id,
            )
        )
        preference.static = real
        session.commit()

    await invoke_registered(Setup(bot), "demo-refresh", interaction)

    assert "not the verified fictional Loot Demo" in interaction.messages[0]["content"]
    with bot.session_factory() as session:
        assert session.get(Static, real_id).name == "Real Static"


async def test_demo_refresh_blocks_open_workflow(bot, interaction_factory):
    await create_demo(bot, interaction_factory)
    with bot.session_factory() as session:
        static = demo_static(session)
        create_reclear_week(
            session,
            static,
            ClearMode.REGULAR,
            today=REGULAR_WEEK,
            actor_discord_user_id=200,
        )
        session.commit()

    interaction = interaction_factory(roles=(BOT_ADMIN_ROLE,))
    await invoke_registered(Setup(bot), "demo-refresh", interaction)
    assert "Close or cancel" in interaction.messages[0]["content"]


def _active_characters(static):
    return [
        character
        for member in static.members
        if member.active
        for character in member.characters
        if character.active
    ]


def complete_week(session, week, actor_id):
    plan = generate_weekly_loot_plan(session, week.id)
    pairs = [(group.id, floor.id) for group in week.groups for floor in week.raid_tier.floors]
    mark_reclear_floors_complete(session, week.id, pairs, actor_id)
    categories = set()
    for planned in plan.assignments:
        assignment = planned.assignment
        if assignment.intended_character_id is None:
            mark_assignment_disposition(
                session,
                week.static_id,
                assignment.id,
                LootAssignmentState.LEFTOVER,
                "Fictional demo leftover",
                actor_id,
            )
            continue
        categories.add(planned.loot_type.category)
        confirm_loot_received(session, assignment.id, True, actor_id)
        if planned.loot_type.category is LootCategory.COFFER:
            confirm_coffer_redemption(session, assignment.id, True, actor_id)
        elif planned.loot_type.category is LootCategory.AUGMENTATION_MATERIAL:
            confirm_augmentation_applied(session, assignment.id, True, actor_id)
    close_reclear_week(session, week.id)
    assert week.workflow_state is ReclearWorkflowState.CLOSED
    return categories


async def test_demo_completes_regular_then_later_split_workflow(bot, interaction_factory):
    await create_demo(bot, interaction_factory)
    with bot.session_factory() as session:
        static = demo_static(session)
        regular = create_reclear_week(
            session,
            static,
            ClearMode.REGULAR,
            today=REGULAR_WEEK,
            actor_discord_user_id=200,
        )
        categories = complete_week(session, regular, 200)
        assert {LootCategory.COFFER, LootCategory.AUGMENTATION_MATERIAL} <= categories

        split_main_ids = {member.id for member in static.members[:4]}
        split = create_reclear_week(
            session,
            static,
            ClearMode.SPLIT,
            split_a_main_member_ids=split_main_ids,
            today=SPLIT_WEEK,
            actor_discord_user_id=200,
        )
        assert len(split.groups) == 2
        assert all(len(group.participants) == 8 for group in split.groups)
        assert all(
            sum(row.character.kind is CharacterKind.MAIN for row in group.participants) == 4
            for group in split.groups
        )
        complete_week(session, split, 200)
        session.commit()
