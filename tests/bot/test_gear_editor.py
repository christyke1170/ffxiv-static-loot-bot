import json
from types import SimpleNamespace

import discord
import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    Character,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    CharacterKind,
    DiscordGuild,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Job,
    RaidFloor,
    RaidTier,
    ReclearFloorCompletion,
    ReclearWeek,
    Static,
    StaticMember,
    UserStaticPreference,
)
from app.services import seed_reference_data
from app.services.board import build_static_gear_board
from app.services.formatting import SLOT_LABEL, player_books, summary_table
from bot.commands.gear import Gear
from bot.services.gear import member_character
from bot.views.gear import MAX_COMPONENTS, MAX_MODAL_INPUTS, STATE_LABELS
from tests.bot.fakes import invoke_registered


def arrange_editor(bot, job_code="PLD", *, include_alt=False):
    with bot.session_factory() as session:
        seed_reference_data(session)
        guild = DiscordGuild(discord_guild_id=100, name="Editor Guild")
        tier = RaidTier(code="EDITOR", name="Editor Tier")
        tier.floors = [
            RaidFloor(floor_number=number, name=f"Configured Floor {number}")
            for number in range(1, 5)
        ]
        static = Static(guild=guild, name="Editor Static", active_raid_tier=tier)
        member = StaticMember(
            static=static, discord_user_id=200, display_name="Editor Administrator"
        )
        job = session.scalar(select(Job).where(Job.abbreviation == job_code))
        character = Character(
            static_member=member,
            job=job,
            name=f"Editor {job_code}",
            world="Test World",
            kind=CharacterKind.MAIN,
        )
        session.add_all([static, character])
        if include_alt:
            session.add(
                Character(
                    static_member=member,
                    job=job,
                    name=f"Editor {job_code} Alt",
                    world="Test World",
                    kind=CharacterKind.ALT,
                )
            )
        session.flush()
        session.add(
            UserStaticPreference(guild_id=guild.id, discord_user_id=200, static_id=static.id)
        )
        weapon = session.scalar(select(GearSlot).where(GearSlot.code == GearSlotCode.WEAPON))
        session.add(
            CharacterGearSlot(
                character=character,
                gear_slot=weapon,
                current_classification=GearClassification.CRAFTED_EX,
            )
        )
        session.commit()
        return character.id


def select_component(view, custom_id):
    return next(
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Select) and item.custom_id == custom_id
    )


def button(view, custom_id):
    return next(
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button) and item.custom_id == custom_id
    )


async def open_editor(bot, interaction_factory, job="PLD", *, kind="MAIN"):
    arrange_editor(bot, job, include_alt=kind == "ALT")
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Editor Administrator", kind)
    return interaction, interaction.messages[0]["view"]


def test_gear_set_exposes_display_name_and_main_or_alt(bot):
    command = next(
        command
        for group in Gear(bot).__cog_app_commands__
        for command in group.commands
        if command.name == "set"
    )
    assert [parameter.name for parameter in command.parameters] == ["display_name", "main_or_alt"]
    assert "character_name" not in {parameter.name for parameter in command.parameters}


def test_member_character_resolves_kind_only_inside_selected_static(bot):
    arrange_editor(bot, include_alt=True)
    with bot.session_factory() as session:
        static = session.scalar(select(Static))
        member, main = member_character(session, static, "Editor Administrator", "MAIN")
        _, alt = member_character(session, static, str(member.id), "ALT")
        assert main.kind is CharacterKind.MAIN
        assert alt.kind is CharacterKind.ALT
        assert main.static_member_id == alt.static_member_id == member.id


@pytest.mark.asyncio
async def test_display_name_autocomplete_is_selected_static_scoped(bot, interaction_factory):
    arrange_editor(bot)
    cog = Gear(bot)
    choices = await cog.set_display_name_autocomplete(interaction_factory(), "Editor")
    assert [(choice.name, choice.value) for choice in choices] == [("Editor Administrator", "1")]

    with bot.session_factory() as session:
        other_guild = DiscordGuild(discord_guild_id=101, name="Other Guild")
        other_static = Static(guild=other_guild, name="Other Static")
        session.add(
            StaticMember(
                static=other_static,
                discord_user_id=201,
                display_name="Editor Administrator",
            )
        )
        session.add(other_static)
        session.commit()
    choices = await cog.set_display_name_autocomplete(interaction_factory(), "Editor")
    assert len(choices) == 1


async def test_editor_is_ephemeral_and_slots_are_ordered_with_current_state(
    bot, interaction_factory
):
    interaction, view = await open_editor(bot, interaction_factory)
    message = interaction.messages[0]
    assert message["ephemeral"] is True
    options = select_component(view, "gear-editor:slot").options
    assert [option.label for option in options] == [SLOT_LABEL[code.value] for code in GearSlotCode]
    assert options[0].description == "Current: Crafted / EX"
    assert all(option.description.startswith("Current: ") for option in options)
    assert len(list(view.walk_children())) <= MAX_COMPONENTS
    assert [option.label for option in select_component(view, "gear-editor:state").options] == list(
        STATE_LABELS.values()
    )
    assert "Editor Administrator · Main · PLD" in message["content"]
    assert "Editor PLD" not in message["content"]
    assert "**Books**\nFloor 1: 0\nFloor 2: 0\nFloor 3: 0\nFloor 4: 0" in message["content"]
    assert button(view, "gear-editor:adjust-books").label == "Adjust Books"


async def open_books_modal(view, interaction_factory, **interaction_kwargs):
    interaction = interaction_factory(**interaction_kwargs)
    await view.adjust_books(interaction)
    return interaction, interaction.response.modals[0] if interaction.response.modals else None


def set_modal_values(modal, values):
    for (_, field), value in zip(modal.fields, values, strict=True):
        field._value = value


async def test_adjust_books_modal_uses_readable_labels_and_effective_defaults(
    bot, interaction_factory
):
    character_id = arrange_editor(bot)
    with bot.session_factory() as session:
        character = session.get(Character, character_id)
        floors = list(session.scalars(select(RaidFloor).order_by(RaidFloor.floor_number)))
        session.add_all(
            [
                CharacterFloorBookBalance(
                    character=character,
                    raid_floor=floors[0],
                    earned=4,
                    spent=2,
                    manual_adjustment=1,
                ),
                CharacterFloorBookBalance(
                    character=character, raid_floor=floors[2], earned=4, manual_adjustment=-1
                ),
            ]
        )
        session.commit()
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Editor Administrator", "MAIN")
    view = interaction.messages[0]["view"]

    opened, modal = await open_books_modal(view, interaction_factory)

    assert opened.messages == []
    assert len(modal.fields) == 4 <= MAX_MODAL_INPUTS
    assert [field.label for _, field in modal.fields] == [
        "Floor 1 Books",
        "Floor 2 Books",
        "Floor 3 Books",
        "Floor 4 Books",
    ]
    assert [field.default for _, field in modal.fields] == ["3", "0", "3", "0"]
    assert all("EDITOR" not in field.label for _, field in modal.fields)
    assert all(
        field.custom_id == f"gear-editor:book:{index}"
        for index, (_, field) in enumerate(modal.fields)
    )


async def test_adjust_books_updates_all_floors_and_refreshes_without_success_message(
    bot, interaction_factory
):
    character_id = arrange_editor(bot)
    with bot.session_factory() as session:
        character = session.get(Character, character_id)
        first = session.scalar(select(RaidFloor).where(RaidFloor.floor_number == 1))
        session.add(
            CharacterFloorBookBalance(character=character, raid_floor=first, earned=4, spent=2)
        )
        session.commit()
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Editor Administrator", "MAIN")
    view = interaction.messages[0]["view"]
    _, modal = await open_books_modal(view, interaction_factory)
    set_modal_values(modal, ["5", "0", "2", "7"])
    submitted = interaction_factory()

    await modal.on_submit(submitted)

    assert submitted.messages == []
    assert submitted.response.edits == [{"content": view.content, "view": view}]
    assert "Floor 1: 5\nFloor 2: 0\nFloor 3: 2\nFloor 4: 7" in view.content
    with bot.session_factory() as session:
        rows = list(
            session.scalars(
                select(CharacterFloorBookBalance)
                .where(CharacterFloorBookBalance.character_id == character_id)
                .order_by(CharacterFloorBookBalance.raid_floor_id)
            )
        )
        assert [(row.earned, row.spent, row.manual_adjustment, row.available) for row in rows] == [
            (4, 2, 3, 5),
            (0, 0, 0, 0),
            (0, 0, 2, 2),
            (0, 0, 7, 7),
        ]
        audits = list(
            session.scalars(select(AuditLog).where(AuditLog.action == "BOOK_AVAILABLE_ADJUSTED"))
        )
        assert len(audits) == 4
        assert json.loads(audits[0].details)["new_effective_balance"] == 5


@pytest.mark.parametrize("bad", ["", "-1", "1.5", "books", "1000001"])
async def test_adjust_books_rejects_invalid_values_without_changing_any_floor(
    bot, interaction_factory, bad
):
    character_id = arrange_editor(bot)
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Editor Administrator", "MAIN")
    view = interaction.messages[0]["view"]
    _, modal = await open_books_modal(view, interaction_factory)
    set_modal_values(modal, ["9", bad, "8", "7"])
    submitted = interaction_factory()

    await modal.on_submit(submitted)

    assert submitted.messages[0]["ephemeral"] is True
    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(CharacterFloorBookBalance).where(
                    CharacterFloorBookBalance.character_id == character_id
                )
            )
            is None
        )


async def test_adjust_books_revalidates_owner_permission_and_selected_static(
    bot, interaction_factory
):
    character_id = arrange_editor(bot)
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Editor Administrator", "MAIN")
    view = interaction.messages[0]["view"]

    another_user, modal = await open_books_modal(view, interaction_factory, user_id=999)
    assert modal is None and another_user.messages[0]["ephemeral"] is True

    _, modal = await open_books_modal(view, interaction_factory)
    set_modal_values(modal, ["1", "1", "1", "1"])
    lost_permission = interaction_factory(roles=())
    await modal.on_submit(lost_permission)
    assert lost_permission.messages[0]["ephemeral"] is True

    with bot.session_factory() as session:
        guild = session.scalar(select(DiscordGuild))
        other = Static(guild=guild, name="Other Selected Static")
        session.add(other)
        session.flush()
        preference = session.scalar(select(UserStaticPreference))
        preference.static_id = other.id
        session.commit()
    cross_static = interaction_factory()
    await modal.on_submit(cross_static)
    assert cross_static.messages[0]["ephemeral"] is True
    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(CharacterFloorBookBalance).where(
                    CharacterFloorBookBalance.character_id == character_id
                )
            )
            is None
        )


async def test_adjust_books_fails_clearly_when_tier_exceeds_modal_limit(bot, interaction_factory):
    arrange_editor(bot)
    with bot.session_factory() as session:
        tier = session.scalar(select(RaidTier))
        tier.floors.extend(
            [
                RaidFloor(floor_number=5, name="Too Many Five"),
                RaidFloor(floor_number=6, name="Too Many Six"),
            ]
        )
        session.commit()
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Editor Administrator", "MAIN")
    view = interaction.messages[0]["view"]
    opened, modal = await open_books_modal(view, interaction_factory)
    assert modal is None
    assert opened.messages == [
        {"content": "This tier has too many floors to adjust in one modal.", "ephemeral": True}
    ]


async def test_adjusted_books_propagate_and_main_alt_weekly_state_stays_separate(
    bot, interaction_factory
):
    main_id = arrange_editor(bot, include_alt=True)
    with bot.session_factory() as session:
        alt_id = session.scalar(select(Character.id).where(Character.kind == CharacterKind.ALT))
        before_weeks = session.scalar(select(func.count()).select_from(ReclearWeek))
        before_completions = session.scalar(
            select(func.count()).select_from(ReclearFloorCompletion)
        )
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Editor Administrator", "MAIN")
    view = interaction.messages[0]["view"]
    _, modal = await open_books_modal(view, interaction_factory)
    set_modal_values(modal, ["4", "3", "2", "1"])
    await modal.on_submit(interaction_factory())

    with bot.session_factory() as session:
        static = session.scalar(select(Static))
        board = build_static_gear_board(session, static.id)
        player = next(row for row in board.players if row.character_id == main_id)
        summary, _ = summary_table(board)
        alt_balances = list(
            session.scalars(
                select(CharacterFloorBookBalance).where(
                    CharacterFloorBookBalance.character_id == alt_id
                )
            )
        )
        assert [book.available for book in player.books] == [4, 3, 2, 1]
        assert "Floor 1 Books: 4" in player_books(player)
        assert "Floor 1: 4" in summary
        assert alt_balances == []
        assert session.scalar(select(func.count()).select_from(ReclearWeek)) == before_weeks
        assert (
            session.scalar(select(func.count()).select_from(ReclearFloorCompletion))
            == before_completions
        )

    alt_interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", alt_interaction, "Editor Administrator", "ALT")
    assert (
        "Floor 1: 0\nFloor 2: 0\nFloor 3: 0\nFloor 4: 0" in alt_interaction.messages[0]["content"]
    )


async def test_selecting_states_saves_refreshes_same_editor_and_supports_multiple_slots(
    bot, interaction_factory
):
    _, view = await open_editor(bot, interaction_factory)
    slot_select = select_component(view, "gear-editor:slot")
    slot_select._values = [GearSlotCode.HEAD.value]
    choose_head = interaction_factory()
    await view.select_slot(choose_head)
    state_select = select_component(view, "gear-editor:state")
    state_select._values = [GearClassification.GARBAGE.value]
    save_head = interaction_factory()
    await view.select_state(save_head)

    select_component(view, "gear-editor:slot")._values = [GearSlotCode.BODY.value]
    await view.select_slot(interaction_factory())
    select_component(view, "gear-editor:state")._values = [GearClassification.SAVAGE.value]
    save_body = interaction_factory()
    await view.select_state(save_body)

    with bot.session_factory() as session:
        rows = {
            row.gear_slot.code: row.current_classification
            for row in session.scalars(select(CharacterGearSlot))
        }
    assert rows[GearSlotCode.HEAD] is GearClassification.GARBAGE
    assert rows[GearSlotCode.BODY] is GearClassification.SAVAGE
    assert save_head.response.edits and save_body.response.edits
    assert save_head.messages == save_body.messages == []
    head = next(
        option
        for option in select_component(view, "gear-editor:slot").options
        if option.value == GearSlotCode.HEAD.value
    )
    assert head.description == "Current: Garbage"


async def test_ex_weapon_rules_and_offhand_applicability(bot, interaction_factory):
    _, pld = await open_editor(bot, interaction_factory, "PLD")
    select_component(pld, "gear-editor:slot")._values = [GearSlotCode.OFFHAND.value]
    await pld.select_slot(interaction_factory())
    assert not select_component(pld, "gear-editor:state").disabled
    select_component(pld, "gear-editor:state")._values = [GearClassification.CRAFTED_EX.value]
    await pld.select_state(interaction_factory())

    other_bot = SimpleNamespace(settings=bot.settings, session_factory=bot.session_factory)
    with other_bot.session_factory() as session:
        session.query(AuditLog).delete()
        session.query(UserStaticPreference).delete()
        session.query(CharacterGearSlot).delete()
        session.query(Character).delete()
        session.query(StaticMember).delete()
        session.query(Static).delete()
        session.query(DiscordGuild).delete()
        session.query(RaidFloor).delete()
        session.query(RaidTier).delete()
        session.commit()
    _, war = await open_editor(other_bot, interaction_factory, "WAR")
    offhand = next(
        option
        for option in select_component(war, "gear-editor:slot").options
        if option.value == GearSlotCode.OFFHAND.value
    )
    assert offhand.description == "Current: N/A"
    select_component(war, "gear-editor:slot")._values = [GearSlotCode.OFFHAND.value]
    await war.select_slot(interaction_factory())
    assert select_component(war, "gear-editor:state").disabled
    assert button(war, "gear-editor:reset").disabled


async def test_reset_and_close_edit_same_message(bot, interaction_factory):
    _, view = await open_editor(bot, interaction_factory)
    select_component(view, "gear-editor:slot")._values = [GearSlotCode.WEAPON.value]
    await view.select_slot(interaction_factory())
    reset = interaction_factory()
    await view.reset_slot(reset)
    with bot.session_factory() as session:
        assert session.scalar(select(CharacterGearSlot)) is None
    closed = interaction_factory()
    await view.close(closed)
    assert view.closed and view.is_finished()
    assert reset.response.edits and closed.response.edits
    assert reset.messages == closed.messages == []
    assert all(getattr(item, "disabled", True) for item in view.walk_children())


async def test_unauthorized_users_cannot_open_or_expose_gear(bot, interaction_factory):
    arrange_editor(bot)
    ordinary = interaction_factory(roles=())
    await invoke_registered(Gear(bot), "set", ordinary, "Editor Administrator", "MAIN")
    assert ordinary.messages == [
        {"content": "You do not have permission to use this command.", "ephemeral": True}
    ]
    unrelated = interaction_factory(user_id=999, roles=())
    await invoke_registered(Gear(bot), "set", unrelated, "Editor Administrator", "MAIN")
    assert unrelated.messages[0]["ephemeral"] is True
    assert "Editor PLD" not in unrelated.messages[0]["content"]


@pytest.mark.parametrize("action", ["select_slot", "select_state", "reset_slot", "close"])
async def test_every_callback_rechecks_owner_and_current_permission(
    bot, interaction_factory, action
):
    _, view = await open_editor(bot, interaction_factory)
    view.selected_slot = GearSlotCode.WEAPON
    view._build()
    select_component(view, "gear-editor:slot")._values = [GearSlotCode.HEAD.value]
    select_component(view, "gear-editor:state")._values = [GearClassification.SAVAGE.value]
    unauthorized = interaction_factory(user_id=999)
    await getattr(view, action)(unauthorized)
    assert unauthorized.messages == [
        {"content": "You cannot use this gear editor.", "ephemeral": True}
    ]

    lost_permission = interaction_factory(roles=())
    await getattr(view, action)(lost_permission)
    assert lost_permission.messages[0]["ephemeral"] is True
    with bot.session_factory() as session:
        weapon = session.scalar(select(CharacterGearSlot))
        assert weapon.current_classification is GearClassification.CRAFTED_EX


async def test_weapon_accepts_ex(bot, interaction_factory):
    _, view = await open_editor(bot, interaction_factory)
    select_component(view, "gear-editor:slot")._values = [GearSlotCode.WEAPON.value]
    await view.select_slot(interaction_factory())
    select_component(view, "gear-editor:state")._values = [GearClassification.CRAFTED_EX.value]
    await view.select_state(interaction_factory())
    with bot.session_factory() as session:
        assert (
            session.scalar(select(CharacterGearSlot)).current_classification
            is GearClassification.CRAFTED_EX
        )
