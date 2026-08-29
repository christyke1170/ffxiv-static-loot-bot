from bot.commands.setup import Setup


def test_setup_has_no_retired_demo_commands(bot):
    names = {
        command.name for group in Setup(bot).__cog_app_commands__ for command in group.commands
    }
    assert {"demo", "demo-refresh"}.isdisjoint(names)
