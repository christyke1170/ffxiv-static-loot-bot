import pytest

from bot.commands.setup import Setup
from tests.bot.fakes import invoke_registered


@pytest.mark.asyncio
async def test_setup_seed_requires_bot_admin(bot, interaction_factory):
    interaction = interaction_factory(roles=())
    await invoke_registered(Setup(bot), "seed", interaction)
    assert "permission" in interaction.messages[0]["content"]
