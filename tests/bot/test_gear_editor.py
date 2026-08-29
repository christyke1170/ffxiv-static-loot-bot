import pytest

from bot.commands.gear import Gear
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static


@pytest.mark.asyncio
async def test_gear_editor_rejects_unknown_member(bot, interaction_factory):
    arrange_static(bot)
    interaction = interaction_factory()
    await invoke_registered(Gear(bot), "set", interaction, "Unknown", "MAIN")
    assert interaction.messages
