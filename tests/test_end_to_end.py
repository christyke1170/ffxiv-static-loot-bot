"""Full fictional regular and split weeks using real commands and services."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.database import Base, create_database_engine, create_session_factory
from app.models import (
    CharacterAugmentationInventory,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    CharacterKind,
    ClearMode,
    DistributionError,
    GearClassification,
    InventoryItem,
    LootCategory,
    LootConfirmation,
    LootReceipt,
    ReclearFloorCompletion,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    WeeklyLockout,
)
from app.schemas.planning import LootPlanGenerationError
from app.services import (
    close_reclear_week,
    confirm_augmentation_applied,
    confirm_coffer_redemption,
    confirm_loot_received,
    confirmation_queue,
    create_reclear_week,
    generate_weekly_loot_plan,
    load_loot_board,
    mark_reclear_floors_complete,
)
from app.services.board import build_static_gear_board
from app.services.loot_formatting import loot_board_table
from bot.commands.bis import Bis
from bot.commands.character import Character as CharacterCog
from bot.commands.gear import Gear
from bot.commands.hierarchy import Hierarchy
from bot.commands.member import Member
from bot.commands.setup import Setup
from bot.commands.static import Static as StaticCog
from bot.commands.tier import Tier
from bot.services.admin import select_static
from bot.views.confirmation import ConfirmationView, first_confirmation_view
from tests.bot.fakes import (
    FakeAttachment,
    FakeDiscordMember,
    FakeInteraction,
    FakeRole,
    invoke_registered,
)

ROOT = Path(__file__).parents[1]
TIER = (ROOT / "tests" / "fixtures" / "e2e_raid_tier.json").read_bytes()
BIS = (ROOT / "tests" / "fixtures" / "e2e_bis_sets.json").read_bytes()
LEADER = 8000
ROLE = 9000


@pytest.fixture
def e2e_bot(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    Base.metadata.create_all(engine)
    bot = SimpleNamespace(
        settings=Settings(bot_admin_role_ids=(ROLE,), raid_leader_role_ids=(ROLE,)),
        session_factory=create_session_factory(engine),
    )
    yield bot
    engine.dispose()


def interaction(bot, user_id=LEADER):
    return FakeInteraction(
        bot,
        user=FakeDiscordMember(user_id, [FakeRole(ROLE)]),
    )


async def arrange_eight_player_static(bot):
    await invoke_registered(Setup(bot), "seed", interaction(bot))
    await invoke_registered(StaticCog(bot), "create", interaction(bot), "Fictional E2E Static", 710)
    with bot.session_factory() as session:
        static = session.scalar(select(Static))
        static_id = static.id
    await invoke_registered(StaticCog(bot), "select", interaction(bot), static_id)
    for index in range(8):
        user_id = LEADER + index
        member = FakeDiscordMember(user_id, [FakeRole(ROLE)])
        await invoke_registered(
            Member(bot), "add", interaction(bot), member, f"Fictional Player {index + 1}"
        )
        with bot.session_factory() as session:
            static = session.get(Static, static_id)
            select_static(session, static.guild_id, user_id, static)
            session.commit()
        await invoke_registered(
            CharacterCog(bot),
            "add",
            interaction(bot, user_id),
            f"Fictional Main {index + 1}",
            "Test World",
            "MAIN",
            "PLD",
        )
        await invoke_registered(
            CharacterCog(bot),
            "add",
            interaction(bot, user_id),
            f"Fictional Alt {index + 1}",
            "Test World",
            "ALT",
            "PLD",
        )
    await invoke_registered(
        Tier(bot), "import", interaction(bot), FakeAttachment(TIER, filename="e2e-tier.json")
    )
    await invoke_registered(Tier(bot), "select", interaction(bot), "E2E_ARC")
    await invoke_registered(
        Bis(bot), "import", interaction(bot), FakeAttachment(BIS, filename="e2e-bis.json")
    )
    for index in range(8):
        await invoke_registered(
            Bis(bot),
            "select",
            interaction(bot),
            f"Fictional Main {index + 1}",
            "Fictional E2E PLD BiS",
            None,
        )
    current = _current_state()
    await invoke_registered(
        Gear(bot),
        "import",
        interaction(bot),
        FakeAttachment(json.dumps(current).encode(), filename="e2e-current.json"),
    )
    await invoke_registered(Hierarchy(bot), "set", interaction(bot), "PLD", False)
    return static_id


def _current_state():
    characters = []
    for index in range(8):
        characters.append(
            {
                "name": f"Fictional Main {index + 1}",
                "world": "Test World",
                "gear_slots": [
                    {
                        "slot": "WEAPON",
                        "current_classification": "CRAFTED_EX",
                    },
                    {
                        "slot": "HEAD",
                        "current_classification": "CRAFTED_EX",
                    },
                ],
                "inventory_items": [
                    {"item": "Fictional Base Earrings", "quantity": 1},
                    {"item": "Fictional Base Armor", "quantity": 1},
                ],
                "books": [
                    {
                        "floor": floor,
                        "earned": (index + floor) % 3,
                        "spent": 0,
                        "manual_adjustment": index % 2,
                    }
                    for floor in range(1, 5)
                ],
                "augmentation_materials": [
                    {"material": "ACCESSORY_GLAZE", "quantity": 0},
                    {"material": "ARMOR_TWINE", "quantity": 0},
                ],
            }
        )
    return {"characters": characters}


async def test_complete_end_to_end_split_week(e2e_bot):
    static_id = await arrange_eight_player_static(e2e_bot)
    with e2e_bot.session_factory() as session:
        static = session.get(Static, static_id)
        mains = sorted(
            (
                character
                for member in static.members
                for character in member.characters
                if character.kind is CharacterKind.MAIN
            ),
            key=lambda row: row.static_member_id,
        )
        selected = {main.static_member_id for main in mains[:4]}
        week = create_reclear_week(
            session,
            static,
            ClearMode.SPLIT,
            split_a_main_member_ids=selected,
            actor_discord_user_id=LEADER,
        )
        session.commit()
        result = generate_weekly_loot_plan(session, week.id)
        repeated_plan = generate_weekly_loot_plan(session, week.id)
        assert repeated_plan.reused_existing_plan
        assert len(session.scalars(select(LootReceipt)).all()) == 0
        initial_gear_board = build_static_gear_board(session, static_id)
        initial_loot_board = load_loot_board(session, static_id)
        assert len(result.assignments) == 8
        assert len(initial_gear_board.players) == 8
        assert len(initial_loot_board.rows) == 8
        assert "E1S" in loot_board_table(initial_loot_board)[0]
        pairs = [(group.id, floor.id) for floor in week.raid_tier.floors for group in week.groups]
        before_books = {
            (row.character_id, row.raid_floor_id): row.earned
            for row in session.scalars(select(CharacterFloorBookBalance))
        }
        mark_reclear_floors_complete(session, week.id, pairs, LEADER)
        session.commit()
        mark_reclear_floors_complete(session, week.id, pairs, LEADER)
        session.commit()
        assert len(session.scalars(select(ReclearFloorCompletion)).all()) == 8
        assert len(session.scalars(select(WeeklyLockout)).all()) == 64
        balances = list(session.scalars(select(CharacterFloorBookBalance)))
        assert len(balances) == 64
        assert all(
            row.earned == before_books.get((row.character_id, row.raid_floor_id), 0) + 1
            for row in balances
        )

        assignments = {row.loot_type.category: [] for row in result.assignments}
        for planned in result.assignments:
            assignments[planned.loot_type.category].append(planned.assignment)
        direct = assignments[LootCategory.GEAR][0]
        coffer = assignments[LootCategory.COFFER][0]
        augments = assignments[LootCategory.AUGMENTATION_MATERIAL]
        for assignment in augments:
            row = session.scalar(
                select(CharacterGearSlot).where(
                    CharacterGearSlot.character_id == assignment.intended_character_id,
                    CharacterGearSlot.gear_slot_id == assignment.intended_bis_set_item.gear_slot_id,
                )
            )
            row.current_classification = GearClassification.TOME
        confirm_loot_received(session, direct.id, True, LEADER)
        confirm_loot_received(session, direct.id, True, LEADER)
        confirm_loot_received(session, coffer.id, True, LEADER)
        confirm_coffer_redemption(session, coffer.id, True, LEADER)
        confirm_loot_received(session, augments[0].id, True, LEADER)
        confirm_augmentation_applied(session, augments[0].id, True, LEADER)
        confirm_loot_received(session, augments[1].id, True, LEADER)
        confirm_augmentation_applied(session, augments[1].id, True, LEADER)
        failed = assignments[LootCategory.GEAR][1]
        confirm_loot_received(session, failed.id, False, LEADER, "fictional misdistribution")
        session.commit()

        pending_before_skip = confirmation_queue(session, week.id)
        skipped = pending_before_skip[0].assignment.id
        view = ConfirmationView(e2e_bot, week.id, skipped)
        skip_interaction = interaction(e2e_bot)
        await view.skip(skip_interaction)
        assert not session.scalar(
            select(LootConfirmation.id).where(LootConfirmation.loot_assignment_id == skipped)
        )
        resumed = first_confirmation_view(e2e_bot, session, week.id)
        assert resumed.assignment_id == skipped

        while queue := confirmation_queue(session, week.id):
            item = queue[0]
            if item.question.value == "RECEIVED":
                confirm_loot_received(session, item.assignment.id, True, LEADER)
            elif item.question.value == "REDEEMED_CORRECTLY":
                confirm_coffer_redemption(session, item.assignment.id, True, LEADER)
            else:
                confirm_augmentation_applied(session, item.assignment.id, True, LEADER)
            session.commit()
        close_reclear_week(session, week.id)
        close_reclear_week(session, week.id)
        session.commit()

        receipt_count = session.scalar(select(func.count()).select_from(LootReceipt))
        inventory = list(session.scalars(select(InventoryItem)))
        material_inventory = list(session.scalars(select(CharacterAugmentationInventory)))
        assert receipt_count == 7
        assert all(row.quantity >= 0 for row in inventory + material_inventory)
        assert len({row.loot_assignment_id for row in session.scalars(select(LootReceipt))}) == 7
        assert session.scalar(select(func.count()).select_from(DistributionError)) == 1
        assert session.get(ReclearWeek, week.id).workflow_state is ReclearWorkflowState.CLOSED
        assert session.scalar(select(func.count()).select_from(LootConfirmation)) >= 11
        failed_slot = session.scalar(
            select(CharacterGearSlot).where(
                CharacterGearSlot.character_id == failed.intended_character_id,
                CharacterGearSlot.gear_slot_id == failed.intended_bis_set_item.gear_slot_id,
            )
        )
        assert failed_slot.current_classification is not failed.intended_bis_set_item.classification
        final_gear_board = build_static_gear_board(session, static_id)
        final_loot_board = load_loot_board(session, static_id)
        assert sum(player.complete_slots for player in final_gear_board.players) > sum(
            player.complete_slots for player in initial_gear_board.players
        )
        assert any(row.status.value == "RECEIPT_FAILED" for row in final_loot_board.rows)

        counts = (
            len(session.scalars(select(ReclearFloorCompletion)).all()),
            len(session.scalars(select(WeeklyLockout)).all()),
            len(session.scalars(select(LootReceipt)).all()),
            len(session.scalars(select(LootConfirmation)).all()),
        )
        with pytest.raises(LootPlanGenerationError, match="closed"):
            generate_weekly_loot_plan(session, week.id)
        from app.schemas.confirmations import ConfirmationError

        with pytest.raises(ConfirmationError, match="closed"):
            mark_reclear_floors_complete(session, week.id, pairs, LEADER)
        with pytest.raises(ConfirmationError, match="closed"):
            confirm_loot_received(session, direct.id, True, LEADER)
        close_reclear_week(session, week.id)
        session.commit()
        assert counts == (
            len(session.scalars(select(ReclearFloorCompletion)).all()),
            len(session.scalars(select(WeeklyLockout)).all()),
            len(session.scalars(select(LootReceipt)).all()),
            len(session.scalars(select(LootConfirmation)).all()),
        )


async def test_complete_regular_week(e2e_bot):
    static_id = await arrange_eight_player_static(e2e_bot)
    with e2e_bot.session_factory() as session:
        static = session.get(Static, static_id)
        week = create_reclear_week(session, static, ClearMode.REGULAR, actor_discord_user_id=LEADER)
        plan = generate_weekly_loot_plan(session, week.id)
        pairs = [(week.groups[0].id, floor.id) for floor in week.raid_tier.floors]
        mark_reclear_floors_complete(session, week.id, pairs, LEADER)
        for planned in plan.assignments:
            confirm_loot_received(session, planned.assignment.id, True, LEADER)
            if planned.loot_type.category is LootCategory.COFFER:
                confirm_coffer_redemption(session, planned.assignment.id, True, LEADER)
            elif planned.loot_type.category is LootCategory.AUGMENTATION_MATERIAL:
                row = session.scalar(
                    select(CharacterGearSlot).where(
                        CharacterGearSlot.character_id == planned.assignment.intended_character_id,
                        CharacterGearSlot.gear_slot_id
                        == planned.assignment.intended_bis_set_item.gear_slot_id,
                    )
                )
                row.current_classification = GearClassification.TOME
                confirm_augmentation_applied(session, planned.assignment.id, True, LEADER)
        close_reclear_week(session, week.id)
        session.commit()
        assert week.workflow_state is ReclearWorkflowState.CLOSED
        assert len(session.scalars(select(ReclearFloorCompletion)).all()) == 4
        assert len(session.scalars(select(WeeklyLockout)).all()) == 32
