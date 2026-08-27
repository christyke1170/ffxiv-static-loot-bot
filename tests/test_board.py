"""Neutral gear-board, formatter, and Components V2 tests."""

from dataclasses import replace
from types import SimpleNamespace

import discord
import pytest
from sqlalchemy import select

from app.models import (
    BisSet,
    BisSetItem,
    Character,
    CharacterBisSelection,
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
    Static,
    StaticMember,
)
from app.schemas.board import DisplayStatus
from app.schemas.needs import NeedStatus, SlotNeedResult
from app.services.board import (
    build_static_gear_board,
    display_status,
)
from app.services.formatting import (
    DISCORD_TEXT_LIMIT,
    LEGEND,
    OVERVIEW_COLUMN_WIDTH,
    STATUS_SYMBOL,
    classification_label,
    loot_type_label,
    material_label,
    overview_table,
    player_table,
    summary_table,
)
from app.services.gearboard import classify_gear_state
from app.services.seed import seed_reference_data
from bot.commands.gear import Gear
from bot.commands.resources import Augment, Books, Inventory
from bot.views.gearboard import (
    MAX_COMPONENTS_V2_CHILDREN,
    STATIC_OVERVIEW_VALUE,
    GearBoardView,
)
from tests.bot.fakes import FakeGuild, FakeResponse


@pytest.fixture
def full_board(session):
    seed_reference_data(session)
    guild = DiscordGuild(discord_guild_id=555, name="Fictional Board Guild")
    tier = RaidTier(code="BOARD", name="Fictional Board Tier")
    tier.floors = [
        RaidFloor(floor_number=number, name=f"Board Floor {number}") for number in range(1, 5)
    ]
    static = Static(guild=guild, name="Fictional Eight", active_raid_tier=tier)
    job = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
    desired_by_slot = {}
    bis_set = BisSet(
        job=job,
        raid_tier=tier,
        name="Fictional Board BiS",
        gear_set_url="https://example.invalid/board",
    )
    for slot in slots:
        desired_by_slot[slot.code] = slot.code.value
        bis_set.items.append(BisSetItem(gear_slot=slot, classification=GearClassification.SAVAGE))
    session.add_all([static, bis_set])
    for index in range(8):
        member = StaticMember(
            static=static, discord_user_id=1000 + index, display_name=f"Player {index + 1}"
        )
        main = Character(
            static_member=member,
            job=job,
            name=f"Main {index + 1}",
            world="Sample",
            kind=CharacterKind.MAIN,
        )
        alt = Character(
            static_member=member,
            job=job,
            name=f"Alt {index + 1}",
            world="Sample",
            kind=CharacterKind.ALT,
        )
        session.add_all(
            [main, alt, CharacterBisSelection(character=main, raid_tier=tier, bis_set=bis_set)]
        )
        if index == 0:
            for slot in slots:
                main.gear_slots.append(
                    CharacterGearSlot(
                        gear_slot=slot,
                        current_classification=GearClassification.SAVAGE,
                    )
                )
            session.add_all(
                [
                    CharacterFloorBookBalance(
                        character=main,
                        raid_floor=tier.floors[0],
                        earned=4,
                        spent=2,
                        manual_adjustment=-1,
                    ),
                    CharacterFloorBookBalance(
                        character=main,
                        raid_floor=tier.floors[2],
                        earned=1,
                    ),
                ]
            )
    session.commit()
    return build_static_gear_board(session, static.id), static


def test_board_contains_eight_mains_and_excludes_alts(full_board):
    board, _ = full_board
    assert len(board.players) == 8
    assert all(player.character_name.startswith("Main") for player in board.players)


def test_board_desired_and_current_categories_are_distinct(full_board):
    board, _ = full_board
    first, second = board.players[:2]
    assert first.slots[0].desired_classification is GearClassification.SAVAGE
    assert first.slots[0].current_classification is GearClassification.SAVAGE
    assert second.slots[0].current_classification is None


def test_board_ring_slots_are_distinct(full_board):
    board, _ = full_board
    rings = [
        slot
        for slot in board.players[0].slots
        if slot.code in {GearSlotCode.RING_1, GearSlotCode.RING_2}
    ]
    assert [slot.code for slot in rings] == [GearSlotCode.RING_1, GearSlotCode.RING_2]
    assert rings[0].code is not rings[1].code


def test_missing_bis_selection_warning(full_board, session):
    _, static = full_board
    selection = session.scalar(
        select(CharacterBisSelection).order_by(CharacterBisSelection.id.desc())
    )
    session.delete(selection)
    session.commit()
    board = build_static_gear_board(session, static.id)
    assert any(
        "no selected BiS" in warning for player in board.players for warning in player.warnings
    )
    missing = next(player for player in board.players if player.warnings)
    assert len(missing.slots) == 12
    assert all(slot.display_status is DisplayStatus.NEEDS_REPLACEMENT for slot in missing.slots)


def need_result(
    status,
    *,
    current_level=None,
    desired_level=None,
    current_classification=GearClassification.CRAFTED_EX,
    job="PLD",
    slot_code=GearSlotCode.HEAD,
):
    character = Character(name="Test", world="World", kind=CharacterKind.MAIN)
    character.job = Job(
        abbreviation=job,
        name=job,
        role="Test",
        uses_offhand=job == "PLD",
    )
    bis_set = BisSet(name="Set")
    slot = GearSlot(code=slot_code, display_name=slot_code.title(), sort_order=1)
    if current_level is not None:
        character.gear_slots.append(
            CharacterGearSlot(
                gear_slot=slot,
                current_classification=current_classification,
            )
        )
    return SlotNeedResult(
        character,
        bis_set,
        slot,
        GearClassification.GARBAGE,
        current_classification,
        status,
    )


@pytest.mark.parametrize(
    ("status", "current", "desired", "classification", "expected"),
    [
        (NeedStatus.COMPLETE, None, None, GearClassification.GARBAGE, DisplayStatus.BIS),
        (
            NeedStatus.MANUALLY_COMPLETE,
            None,
            None,
            GearClassification.GARBAGE,
            DisplayStatus.BIS,
        ),
        (
            NeedStatus.NEEDS_CATEGORY,
            700,
            730,
            GearClassification.SAVAGE,
            DisplayStatus.ALTERNATE,
        ),
        (
            NeedStatus.NEEDS_CATEGORY,
            700,
            730,
            GearClassification.AUGMENTED_TOME,
            DisplayStatus.ALTERNATE,
        ),
        (
            NeedStatus.NEEDS_CATEGORY,
            700,
            730,
            GearClassification.TOME,
            DisplayStatus.TOME_NEEDS_AUGMENT,
        ),
        (
            NeedStatus.NEEDS_CATEGORY,
            700,
            730,
            GearClassification.CRAFTED_EX,
            DisplayStatus.CRAFTED_EX,
        ),
        (
            NeedStatus.NEEDS_CATEGORY,
            700,
            730,
            GearClassification.CRAFTED_EX,
            DisplayStatus.CRAFTED_EX,
        ),
        (
            NeedStatus.NEEDS_CATEGORY,
            700,
            730,
            GearClassification.SAVAGE,
            DisplayStatus.ALTERNATE,
        ),
        (
            NeedStatus.NEEDS_CATEGORY,
            None,
            730,
            GearClassification.GARBAGE,
            DisplayStatus.NEEDS_REPLACEMENT,
        ),
        (
            NeedStatus.INVALID_CONFIGURATION,
            None,
            None,
            GearClassification.GARBAGE,
            DisplayStatus.NEEDS_REPLACEMENT,
        ),
        (
            NeedStatus.NOT_APPLICABLE,
            None,
            None,
            GearClassification.GARBAGE,
            DisplayStatus.NEEDS_REPLACEMENT,
        ),
    ],
)
def test_every_display_status_mapping(status, current, desired, classification, expected):
    assert (
        display_status(
            need_result(
                status,
                current_level=current,
                desired_level=desired,
                current_classification=classification,
            )
        )
        is expected
    )


def test_non_pld_offhand_is_always_na():
    result = need_result(
        NeedStatus.INVALID_CONFIGURATION,
        current_level=1,
        current_classification=GearClassification.GARBAGE,
        job="WAR",
        slot_code=GearSlotCode.OFFHAND,
    )
    assert display_status(result) is DisplayStatus.NA


@pytest.mark.parametrize(
    ("desired_classification", "current_classification"),
    [
        (GearClassification.SAVAGE, GearClassification.CRAFTED_EX),
        (GearClassification.AUGMENTED_TOME, GearClassification.CRAFTED_EX),
        (GearClassification.TOME, GearClassification.CRAFTED_EX),
    ],
)
def test_crafted_current_gear_is_yellow_independent_of_desired_classification(
    desired_classification, current_classification
):
    result = need_result(
        NeedStatus.NEEDS_CATEGORY,
        current_level=710,
        desired_level=730,
        current_classification=current_classification,
    )
    result.desired_classification = desired_classification
    assert classify_gear_state(result) is DisplayStatus.CRAFTED_EX


def test_exact_base_tome_item_is_bis_even_when_current_classification_is_tome():
    result = need_result(
        NeedStatus.COMPLETE,
        current_level=710,
        desired_level=710,
        current_classification=GearClassification.TOME,
    )
    assert classify_gear_state(result) is DisplayStatus.BIS


def test_base_tome_for_augmented_desired_item_needs_augment():
    result = need_result(
        NeedStatus.NEEDS_AUGMENTATION,
        current_level=710,
        desired_level=730,
        current_classification=GearClassification.TOME,
    )
    assert classify_gear_state(result) is DisplayStatus.TOME_NEEDS_AUGMENT


def test_exact_base_tome_for_augmented_desired_item_needs_augment():
    result = need_result(
        NeedStatus.NEEDS_BASE_TOME_ITEM,
        current_level=710,
        desired_level=730,
        current_classification=GearClassification.TOME,
    )
    result.desired_classification = GearClassification.AUGMENTED_TOME
    assert classify_gear_state(result) is DisplayStatus.TOME_NEEDS_AUGMENT


@pytest.mark.parametrize("current_level", [1, 710, 999999])
def test_arbitrary_item_levels_do_not_change_source_status(current_level):
    result = need_result(
        NeedStatus.NEEDS_CATEGORY,
        current_level=current_level,
        desired_level=730,
        current_classification=GearClassification.SAVAGE,
    )
    assert classify_gear_state(result) is DisplayStatus.ALTERNATE


def test_matching_classification_is_bis_even_with_stale_needs_status():
    result = need_result(
        NeedStatus.NEEDS_CATEGORY,
        current_level=710,
        desired_level=730,
        current_classification=GearClassification.CRAFTED_EX,
    )
    result.desired_classification = GearClassification.CRAFTED_EX
    assert classify_gear_state(result) is DisplayStatus.BIS


def test_different_crafted_item_against_non_crafted_bis_is_yellow():
    result = need_result(
        NeedStatus.NEEDS_CATEGORY,
        current_level=710,
        desired_level=730,
        current_classification=GearClassification.CRAFTED_EX,
    )
    result.desired_classification = GearClassification.SAVAGE
    assert classify_gear_state(result) is DisplayStatus.CRAFTED_EX


def test_garbage_current_gear_is_red():
    result = need_result(
        NeedStatus.NEEDS_CATEGORY,
        current_level=1,
        desired_level=730,
        current_classification=GearClassification.GARBAGE,
    )
    assert classify_gear_state(result) is DisplayStatus.NEEDS_REPLACEMENT
    assert STATUS_SYMBOL[classify_gear_state(result)] == "🟥"


def test_ring_slots_are_classified_independently_by_slot_classification():
    ring_one = need_result(
        NeedStatus.COMPLETE,
        current_level=710,
        desired_level=710,
        current_classification=GearClassification.TOME,
        slot_code=GearSlotCode.RING_1,
    )
    ring_two = need_result(
        NeedStatus.NEEDS_AUGMENTATION,
        current_level=710,
        desired_level=730,
        current_classification=GearClassification.TOME,
        slot_code=GearSlotCode.RING_2,
    )
    assert classify_gear_state(ring_one) is DisplayStatus.BIS
    assert classify_gear_state(ring_two) is DisplayStatus.TOME_NEEDS_AUGMENT


def test_overview_and_detail_use_identical_slot_statuses(full_board):
    board, _ = full_board
    player = board.players[0]
    overview, _ = overview_table(board, 0)
    detail, _ = player_table(player)
    for slot in player.slots:
        marker = STATUS_SYMBOL[slot.display_status]
        assert marker in overview
        assert marker in detail


def test_four_player_pagination(full_board):
    board, _ = full_board
    first, _ = overview_table(board, 0)
    second, _ = overview_table(board, 1)
    assert "Player 1" in first and "Player 4" in first and "Player 5" not in first
    assert "Player 5" in second and "Player 8" in second and "Player 4" not in second


def test_overview_is_a_player_summary(full_board):
    board, _ = full_board
    completed = replace(board.players[0], complete_slots=12, applicable_slots=12)
    overview, _ = overview_table(replace(board, players=(completed, *board.players[1:])))
    assert "Player 1 · PLD · Main" in overview
    assert "12/12" in overview
    assert all(label in overview for label in ("Savage ", "Augment ", "Tome "))
    assert all(
        label in overview
        for label in (
            "Weapon",
            "Offhand",
            "Hat",
            "Earring",
            "Chest",
            "Necklace",
            "Gloves",
            "Bracelet",
            "Pants",
            "Ring 1",
            "Boots",
            "Ring 2",
        )
    )


def test_overview_has_exact_fixed_width_slot_pairs_and_icon_prefixes(full_board):
    board, _ = full_board
    overview, _ = overview_table(board, 0)
    slot_lines = [
        line
        for line in overview.splitlines()
        if any(line.startswith(icon) for icon in STATUS_SYMBOL.values())
    ]

    assert len(slot_lines) == 24
    expected_labels = (
        ("Weapon", "Offhand"),
        ("Hat", "Earring"),
        ("Chest", "Necklace"),
        ("Gloves", "Bracelet"),
        ("Pants", "Ring 1"),
        ("Boots", "Ring 2"),
    )
    for player_offset in range(0, len(slot_lines), 6):
        rows = slot_lines[player_offset : player_offset + 6]
        for row, (first_label, second_label) in zip(rows, expected_labels, strict=True):
            assert any(row.startswith(icon + " ") for icon in STATUS_SYMBOL.values())
            assert any(icon + " " + second_label in row for icon in STATUS_SYMBOL.values())
            assert first_label in row and second_label in row
        second_icon_positions = [
            min(row.index(icon) for icon in STATUS_SYMBOL.values() if icon in row[1:])
            for row in rows
        ]
        assert len(set(second_icon_positions)) == 1


def test_overview_slots_are_unique_and_status_icons_precede_labels(full_board):
    board, _ = full_board
    overview, _ = overview_table(board, 0)
    for label in (
        "Weapon",
        "Offhand",
        "Hat",
        "Earring",
        "Chest",
        "Necklace",
        "Gloves",
        "Bracelet",
        "Pants",
        "Ring 1",
        "Boots",
        "Ring 2",
    ):
        assert overview.count(label) == 4
    slot_lines = [
        line
        for line in overview.splitlines()
        if any(line.startswith(icon) for icon in STATUS_SYMBOL.values())
    ]
    assert len(slot_lines) == 24
    assert all(
        any(line.startswith(icon) for icon in STATUS_SYMBOL.values())
        and any(icon in line[OVERVIEW_COLUMN_WIDTH:] for icon in STATUS_SYMBOL.values())
        for line in slot_lines
    )


def test_overview_long_player_name_does_not_break_fixed_width_table(full_board):
    board, _ = full_board
    player = replace(board.players[0], display_name="A" * 200)
    overview, _ = overview_table(replace(board, players=(player,)))
    slot_lines = [
        line
        for line in overview.splitlines()
        if any(line.startswith(icon) for icon in STATUS_SYMBOL.values())
    ]
    second_icon_positions = [
        min(line.index(icon) for icon in STATUS_SYMBOL.values() if icon in line[1:])
        for line in slot_lines
    ]
    assert len(set(second_icon_positions)) == 1


def test_overview_contains_no_legacy_grid_or_image_generation(full_board):
    board, _ = full_board
    overview, _ = overview_table(board)
    assert all(legacy not in overview for legacy in ("Wpn", "OH", "Head", "Body", "R1", "R2"))
    assert "discord.File" not in overview
    assert ".png" not in overview.lower()


def test_overview_uses_only_status_symbols_and_legend_is_complete(full_board):
    board, _ = full_board
    statuses = tuple(DisplayStatus)
    slots = tuple(
        replace(board.players[0].slots[index], display_status=status)
        for index, status in enumerate(statuses)
    )
    player = replace(board.players[0], slots=slots)
    overview, _ = overview_table(replace(board, players=(player,)))

    assert set(STATUS_SYMBOL) == set(statuses)
    assert all(symbol in LEGEND for symbol in STATUS_SYMBOL.values())
    assert all(symbol in overview for symbol in STATUS_SYMBOL.values())
    assert all(icon not in overview for icon in ("⚔", "🪙", "✨", "📚", "📦", "➖", "✅"))


def test_long_names_backticks_and_length_are_safe(full_board):
    board, _ = full_board
    player = replace(board.players[0], display_name="`" + "VeryLongName" * 30)
    long_board = replace(board, players=(player, *board.players[1:]))
    table, _ = overview_table(long_board)
    assert table.count("```") == 2
    assert "VeryLongNameVeryLongNameVeryLongName" not in table
    assert len(table) <= DISCORD_TEXT_LIMIT


def test_detailed_player_and_summary_tables(full_board):
    board, _ = full_board
    detail, _ = player_table(board.players[0])
    summary, _ = summary_table(board)
    assert all(label in detail for label in ("Desired", "Current", "Status", "Ring 1", "Ring 2"))
    assert all(label in summary for label in ("Static Progress", "Current Week", "Player Progress"))
    assert "Remaining Savage Drops" in summary
    assert "Augmentation Needed" in summary
    assert "Books Per Player" in summary
    assert "🟩" not in summary
    assert classification_label(GearClassification.CRAFTED_EX) == "Crafted / EX"
    assert material_label("ACCESSORY_GLAZE") == "Glaze"
    assert material_label("ARMOR_TWINE") == "Twine"
    assert loot_type_label("HEAD_COFFER") == "Heads"
    assert "HEAD_COFFER" not in summary


def test_selected_player_detail_shows_effective_books_only(full_board):
    board, _ = full_board
    view = GearBoardView(SimpleNamespace(), board, mode=f"player:{board.players[0].character_id}")
    text = "\n".join(
        item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)
    )
    assert "**Books**" in text
    assert "Floor 1 Books: 1" in text
    assert "Floor 2 Books: 0" in text
    assert "Floor 3 Books: 1" in text
    assert "Floor 4 Books: 0" in text
    displays = [
        item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)
    ]
    assert "Floor 4 Books: 0" in displays[1]
    assert LEGEND in displays[2]
    assert all(raw not in text for raw in ("BOARD", "CharacterFloorBookBalance"))

    overview = GearBoardView(SimpleNamespace(), board)
    summary = GearBoardView(SimpleNamespace(), board, mode="summary")
    overview_text = "\n".join(
        item.content
        for item in overview.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )
    summary_text = "\n".join(
        item.content for item in summary.walk_children() if isinstance(item, discord.ui.TextDisplay)
    )
    assert "Floor 1 Books:" not in overview_text
    assert "Books Per Player" in summary_text


class EditResponse(FakeResponse):
    def __init__(self):
        super().__init__()
        self.edits = []

    async def edit_message(self, **kwargs):
        self._done = True
        self.edits.append(kwargs)


def callback_interaction(bot, guild_id=555):
    return SimpleNamespace(
        guild=FakeGuild(guild_id), response=EditResponse(), user=SimpleNamespace(id=1000)
    )


def button(view, custom_id):
    return next(
        item for item in view.walk_children() if getattr(item, "custom_id", None) == custom_id
    )


def player_select(view):
    return button(view, "gearboard:player")


def test_components_v2_construction_and_limit(full_board):
    board, _ = full_board
    view = GearBoardView(SimpleNamespace(), board)
    assert view.has_components_v2()
    assert isinstance(view.children[0], discord.ui.Container)
    assert view.total_children_count <= MAX_COMPONENTS_V2_CHILDREN
    assert {
        button(view, f"gearboard:{name}").label
        for name in ("previous", "next", "refresh", "summary", "close")
    } == {"Previous", "Next", "Refresh", "Summary", "Close"}
    assert player_select(view).options[0].label == "Static Overview"
    assert player_select(view).options[0].description == "Return to the full gearboard"
    assert player_select(view).options[0].value == STATIC_OVERVIEW_VALUE
    assert STATIC_OVERVIEW_VALUE not in {str(player.character_id) for player in view.board.players}


async def test_previous_next_summary_and_close_callbacks(full_board):
    board, _ = full_board
    view = GearBoardView(SimpleNamespace(), board)
    interaction = callback_interaction(None)
    await view.next_page(interaction)
    assert view.page == 1
    await view.previous_page(callback_interaction(None))
    assert view.page == 0
    await view.show_summary(callback_interaction(None))
    assert view.mode == "summary"
    await view.close(callback_interaction(None))
    assert view.closed and all(getattr(item, "disabled", True) for item in view.walk_children())


async def test_player_callback(full_board):
    board, _ = full_board
    view = GearBoardView(SimpleNamespace(), board)
    select_menu = player_select(view)
    select_menu._values = [str(board.players[2].character_id)]
    await view.select_player(callback_interaction(None))
    assert view.mode == f"player:{board.players[2].character_id}"


async def test_static_overview_returns_to_remembered_page_without_sending(full_board):
    board, _ = full_board
    view = GearBoardView(SimpleNamespace(), board, page=1)
    select_menu = player_select(view)
    select_menu._values = [str(board.players[2].character_id)]
    await view.select_player(callback_interaction(None))
    assert view.mode == f"player:{board.players[2].character_id}"

    interaction = callback_interaction(None)
    player_select(view)._values = [STATIC_OVERVIEW_VALUE]
    await view.select_player(interaction)

    assert view.mode == "overview"
    assert view.page == 1
    assert view.selected_player_id is None
    assert player_select(view).options[0].value == STATIC_OVERVIEW_VALUE
    assert interaction.response.edits == [{"view": view}]
    assert interaction.response.messages == []


async def test_static_overview_option_appears_on_detail_and_direct_player_navigation(full_board):
    board, _ = full_board
    view = GearBoardView(SimpleNamespace(), board, page=1)
    player_select(view)._values = [str(board.players[0].character_id)]
    await view.select_player(callback_interaction(None))
    assert player_select(view).options[0].value == STATIC_OVERVIEW_VALUE

    player_select(view)._values = [str(board.players[3].character_id)]
    await view.select_player(callback_interaction(None))
    assert view.mode == f"player:{board.players[3].character_id}"
    assert view.selected_player_id == board.players[3].character_id


async def test_refresh_preserves_overview_and_detail_modes(full_board, session):
    board, static = full_board
    bot = SimpleNamespace(session_factory=lambda: session)
    static.name = "Refreshed Name"
    session.commit()
    view = GearBoardView(bot, board, page=1)
    player_select(view)._values = [str(board.players[2].character_id)]
    await view.select_player(callback_interaction(bot))
    await view.refresh(callback_interaction(bot))
    assert view.mode == f"player:{board.players[2].character_id}"
    assert view.selected_player_id == board.players[2].character_id

    player_select(view)._values = [STATIC_OVERVIEW_VALUE]
    await view.select_player(callback_interaction(bot))
    await view.refresh(callback_interaction(bot))
    assert view.mode == "overview"
    assert view.page == 1
    assert view.board.static_name == "Refreshed Name"


async def test_refresh_reloads_database(full_board, session):
    board, static = full_board
    bot = SimpleNamespace(session_factory=lambda: session)
    view = GearBoardView(bot, board)
    static.name = "Refreshed Name"
    session.commit()
    await view.refresh(callback_interaction(bot))
    assert view.board.static_name == "Refreshed Name"


async def test_stale_static_refresh_is_handled(full_board, session):
    board, static = full_board
    static.active = False
    session.commit()
    view = GearBoardView(SimpleNamespace(session_factory=lambda: session), board)
    await view.refresh(callback_interaction(None))
    assert view.is_finished()
    assert all(getattr(item, "disabled", True) for item in view.walk_children())


async def test_outside_guild_interaction_rejected(full_board):
    board, _ = full_board
    view = GearBoardView(SimpleNamespace(), board)
    interaction = callback_interaction(None, guild_id=999)
    assert not await view.interaction_check(interaction)


async def test_in_guild_nonmember_interaction_rejected(full_board):
    board, _ = full_board
    view = GearBoardView(SimpleNamespace(), board)
    interaction = callback_interaction(None)
    interaction.user.id = 999999
    assert not await view.interaction_check(interaction)


@pytest.mark.parametrize(
    ("cog_type", "command"),
    [
        (Gear, "set"),
        (Gear, "clear"),
        (Gear, "complete"),
        (Gear, "import"),
        (Inventory, "set"),
        (Augment, "set"),
        (Books, "set"),
    ],
)
def test_all_new_write_commands_have_permission_checks(cog_type, command):
    cog = cog_type(SimpleNamespace())
    registered = next(
        value
        for group in cog.__cog_app_commands__
        for value in group.commands
        if value.name == command
    )
    assert registered.checks
