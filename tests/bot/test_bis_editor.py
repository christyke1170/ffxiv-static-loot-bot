import discord
from sqlalchemy import select

from app.models import BisSet, BisSetItem, GearClassification, GearSlotCode
from app.services.seed import seed_reference_data
from bot.commands.bis import Bis
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static


def _seed(bot):
    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()


async def test_bis_set_renders_valid_v2_payload_and_sam_offhand_default(bot, interaction_factory):
    _seed(bot)
    arrange_static(bot)
    interaction = interaction_factory()

    await invoke_registered(Bis(bot), "set", interaction, "SAM")

    message = interaction.followup.messages[0]
    view = message["view"]
    payload = view.to_components()
    assert message["ephemeral"] is True
    assert message["content"] is None
    assert all(component["type"] in {1, 9, 10, 12, 13, 14, 17} for component in payload)
    assert payload[0]["type"] == 17
    assert [child["type"] for child in payload[0]["components"][:-1]] == [10, 1] * 12
    assert payload[0]["components"][-1]["type"] == 1
    assert view.categories[GearSlotCode.OFFHAND] is GearClassification.NOT_APPLICABLE
    labels = {
        option.label
        for row in view.walk_children()
        if isinstance(row, discord.ui.Select)
        for option in row.options
    }
    assert labels == {"CRAFTED_EX", "TOME", "AUGMENTED_TOME", "SAVAGE", "NOT_APPLICABLE"}


async def test_bis_selection_callback_edits_only_with_v2_view(bot, interaction_factory):
    _seed(bot)
    arrange_static(bot)
    initial = interaction_factory()
    await invoke_registered(Bis(bot), "set", initial, "SAM")
    view = initial.followup.messages[0]["view"]
    select = next(
        child
        for child in view.walk_children()
        if getattr(child, "custom_id", None) == "bis-editor:slot:WEAPON"
    )
    select._values = [GearClassification.SAVAGE.value]
    selected = interaction_factory()

    await view._select_callback(GearSlotCode.WEAPON)(selected)

    assert "content" not in selected.response.edits[0]
    assert "embeds" not in selected.response.edits[0]
    assert selected.response.edits[0]["view"] is view


async def test_bis_save_error_replaces_editor_with_v2_error_layout(bot, interaction_factory):
    _seed(bot)
    arrange_static(bot)
    initial = interaction_factory()
    await invoke_registered(Bis(bot), "set", initial, "SAM")
    view = initial.followup.messages[0]["view"]
    view.categories[GearSlotCode.WEAPON] = GearClassification.NOT_APPLICABLE
    failed = interaction_factory()

    await view.save(failed)

    edit = failed.response.edits[0]
    assert "content" not in edit
    assert "embeds" not in edit
    assert "NOT_APPLICABLE" in edit["view"].to_components()[0]["components"][0]["content"]


async def test_bis_editor_timeout_edits_only_with_v2_view(bot, interaction_factory):
    _seed(bot)
    arrange_static(bot)
    initial = interaction_factory()
    await invoke_registered(Bis(bot), "set", initial, "SAM")
    view = initial.followup.messages[0]["view"]

    class FakeMessage:
        def __init__(self):
            self.edits = []

        async def edit(self, **kwargs):
            self.edits.append(kwargs)

    message = FakeMessage()
    view.message = message
    await view.on_timeout()

    assert "content" not in message.edits[0]
    assert "embeds" not in message.edits[0]
    assert message.edits[0]["view"] is view


async def test_bis_editor_save_reopen_and_cancel(bot, interaction_factory):
    _seed(bot)
    static_id = arrange_static(bot)
    command = Bis(bot)
    interaction = interaction_factory()
    await invoke_registered(command, "set", interaction, "SAM")
    view = interaction.followup.messages[0]["view"]

    saved_interaction = interaction_factory()
    await view.save(saved_interaction)
    assert "content" not in saved_interaction.response.edits[0]
    assert (
        "Saved BiS configuration for SAM."
        in saved_interaction.response.edits[0]["view"].to_components()[0]["components"][0][
            "content"
        ]
    )
    with bot.session_factory() as session:
        bis = session.scalar(select(BisSet).where(BisSet.static_id == static_id))
        assert bis is not None
        assert (
            len(list(session.scalars(select(BisSetItem).where(BisSetItem.bis_set_id == bis.id))))
            == 12
        )

    reopened = interaction_factory()
    await invoke_registered(command, "set", reopened, "SAM")
    reopened_view = reopened.followup.messages[0]["view"]
    assert reopened_view.categories[GearSlotCode.OFFHAND] is GearClassification.NOT_APPLICABLE

    cancelled = interaction_factory()
    await reopened_view.cancel(cancelled)
    assert (
        cancelled.response.edits[0]["view"].to_components()[0]["components"][0]["content"]
        == "BiS editor cancelled; no changes were written."
    )


async def test_bis_set_missing_static_selection_is_clear(bot, interaction_factory):
    _seed(bot)
    arrange_static(bot, selected=False)
    interaction = interaction_factory()

    await invoke_registered(Bis(bot), "set", interaction, "SAM")

    assert "/static select" in interaction.messages[0]["content"]


async def test_bis_set_missing_reference_seed_is_clear(bot, interaction_factory):
    arrange_static(bot)
    interaction = interaction_factory()

    await invoke_registered(Bis(bot), "set", interaction, "SAM")

    assert "/setup seed" in interaction.messages[0]["content"]
