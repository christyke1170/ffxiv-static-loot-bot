"""New command extensions load without a Discord connection."""

import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "bot.commands.gear",
        "bot.commands.resources",
        "bot.commands.needs",
        "bot.commands.gearboard",
    ],
)
async def test_extension_setup_loads_without_connecting(module_name):
    class FakeBot:
        def __init__(self):
            self.cogs = []

        async def add_cog(self, cog):
            self.cogs.append(cog)

    bot = FakeBot()
    module = importlib.import_module(module_name)
    await module.setup(bot)
    assert bot.cogs
