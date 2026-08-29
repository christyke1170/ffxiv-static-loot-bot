import pytest

from bot.commands.hierarchy import Hierarchy
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static


@pytest.mark.asyncio
async def test_hierarchy_command_rejects_unknown_job(bot, interaction_factory):
    arrange_static(bot)
    interaction = interaction_factory()
    await invoke_registered(Hierarchy(bot), "set", interaction, "NOPE", True)
    assert "Unknown jobs" in interaction.messages[0]["content"]
