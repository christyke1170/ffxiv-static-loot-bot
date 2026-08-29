import pytest
from sqlalchemy import select

from app.models import (
    Character,
    CharacterGearSlot,
    CharacterKind,
    GearClassification,
    GearSlotCode,
    Job,
    Static,
    StaticMember,
)
from app.services.seed import seed_reference_data
from bot.commands.gear import Gear
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static


@pytest.mark.asyncio
async def test_gear_editor_rejects_unknown_member(bot, interaction_factory):
    arrange_static(bot)
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Unknown", "MAIN")
    assert interaction.messages


def _arrange_character(bot):
    static_id = arrange_static(bot)
    with bot.session_factory() as session:
        seed_reference_data(session)
        static = session.get(Static, static_id)
        member = StaticMember(static=static, discord_user_id=200, display_name="Player")
        session.add(member)
        job = session.scalar(select(Job).where(Job.abbreviation == "SAM"))
        character = Character(
            static_member=member,
            job=job,
            name="Samurai",
            world="Test",
            kind=CharacterKind.MAIN,
        )
        session.add(character)
        session.commit()
        session.flush()
        return static_id, member.id, character.id


async def test_gear_editor_has_all_labeled_slot_dropdowns_and_adjust_books(
    bot, interaction_factory
):
    from bot.views.gear import GearEditorView

    _, member_id, character_id = _arrange_character(bot)
    view = GearEditorView(bot, 1, member_id, character_id, 200, 100)

    selects = [child for child in view.walk_children() if hasattr(child, "custom_id")]
    assert [select.custom_id for select in selects[:12]] == [
        f"gear-editor:slot:{code.value}" for code in GearSlotCode
    ]
    assert {option.label for select in selects[:12] for option in select.options} >= {
        "Missing",
        "Crafted / EX",
        "Savage",
        "Tome",
        "Augmented Tome",
    }
    assert any(child.custom_id == "gear-editor:adjust-books" for child in selects)
    assert any(child.custom_id == "gear-editor:save" for child in selects)
    assert any(child.custom_id == "gear-editor:cancel" for child in selects)


async def test_gear_editor_serialized_action_rows_fit_discord_width_limit(bot, interaction_factory):
    from bot.views.gear import GearEditorView

    _, member_id, character_id = _arrange_character(bot)
    view = GearEditorView(bot, 1, member_id, character_id, 200, 100)

    payload = view.to_components()
    rows = [child for child in payload if child["type"] == 1]
    assert all(
        sum(component.get("width", 1) for component in row["components"]) <= 5 for row in rows
    )


async def test_gear_editor_save_persists_all_changes_and_disappears(bot, interaction_factory):
    _, member_id, character_id = _arrange_character(bot)
    from bot.views.gear import GearEditorView

    view = GearEditorView(bot, 1, member_id, character_id, 200, 100)
    weapon = next(
        child
        for child in view.walk_children()
        if getattr(child, "custom_id", None) == "gear-editor:slot:WEAPON"
    )
    weapon._values = [GearClassification.SAVAGE.value]
    await view._select_category(GearSlotCode.WEAPON)(interaction_factory())
    saved = interaction_factory()
    await view.save(saved)

    with bot.session_factory() as session:
        row = session.scalar(
            select(CharacterGearSlot).where(CharacterGearSlot.character_id == character_id)
        )
        assert row.current_classification is GearClassification.SAVAGE
    assert saved.response.edits[0]["view"] is not None
    assert "content" not in saved.response.edits[0]


async def test_gear_editor_cancel_discards_changes_and_disappears(bot, interaction_factory):
    _, member_id, character_id = _arrange_character(bot)
    from bot.views.gear import GearEditorView

    view = GearEditorView(bot, 1, member_id, character_id, 200, 100)
    cancelled = interaction_factory()
    await view.cancel(cancelled)

    with bot.session_factory() as session:
        assert (
            session.scalar(
                select(CharacterGearSlot).where(CharacterGearSlot.character_id == character_id)
            )
            is None
        )
    assert cancelled.response.edits[0]["view"] is not None
    assert "content" not in cancelled.response.edits[0]
