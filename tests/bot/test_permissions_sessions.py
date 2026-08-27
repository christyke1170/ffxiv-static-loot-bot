from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import BisSet, CharacterBisSelection, Job, RaidTier, Static
from app.services import seed_reference_data
from bot.commands.bis import Bis
from bot.commands.character import Character
from bot.commands.gear import Gear
from bot.commands.hierarchy import Hierarchy
from bot.commands.member import Member
from bot.commands.setup import Setup
from bot.commands.static import Static as StaticCog
from bot.commands.tier import Tier
from bot.errors import handle_app_command_error
from bot.services.commands import command_session, pages, reply
from tests.bot.fakes import FakeInteraction, invoke_registered, registered_command
from tests.bot.helpers import arrange_imports, arrange_static


async def test_bis_select_persists_valid_same_tier_selection(bot, interaction_factory):
    arrange_imports(bot, bis=True)
    arrange_static(bot)
    member_interaction = interaction_factory()
    await invoke_registered(
        Member(bot), "add", member_interaction, member_interaction.user, "Player"
    )
    await invoke_registered(
        Character(bot), "add", interaction_factory(), "Hero", "World", "MAIN", "PLD"
    )
    await invoke_registered(Tier(bot), "select", interaction_factory(), "FICTIONAL_ARC")
    interaction = interaction_factory()

    await invoke_registered(Bis(bot), "select", interaction, "Hero", "Fictional PLD Sample", None)

    with bot.session_factory() as session:
        row = session.scalar(select(CharacterBisSelection))
        assert row.character.name == "Hero"
        assert row.bis_set.name == "Fictional PLD Sample"
        assert row.raid_tier.code == "FICTIONAL_ARC"
    assert "none → Fictional PLD Sample" in interaction.messages[0]["content"]


async def test_bis_show_formats_existing_selection(bot, interaction_factory):
    arrange_imports(bot, bis=True)
    arrange_static(bot)
    member_interaction = interaction_factory()
    await invoke_registered(
        Member(bot), "add", member_interaction, member_interaction.user, "Player"
    )
    await invoke_registered(
        Character(bot), "add", interaction_factory(), "Hero", "World", "MAIN", "PLD"
    )
    await invoke_registered(Tier(bot), "select", interaction_factory(), "FICTIONAL_ARC")
    await invoke_registered(
        Bis(bot),
        "select",
        interaction_factory(),
        "Hero",
        "Fictional PLD Sample",
        None,
    )
    interaction = interaction_factory(roles=())

    await invoke_registered(Bis(bot), "show", interaction, "Hero")

    with bot.session_factory() as session:
        selection = session.scalar(select(CharacterBisSelection))
        expected = {
            "Character: Hero",
            "Job: PLD",
            "Set: Fictional PLD Sample",
            f"GCD: {selection.bis_set.gcd_label or 'none'}",
            f"Link: {selection.bis_set.gear_set_url or 'none'}",
            f"Desired slots: {len(selection.bis_set.items)}",
        }
    assert set(interaction.messages[0]["content"].splitlines()) == expected


async def test_cross_tier_bis_selection_is_rejected(bot, interaction_factory):
    arrange_imports(bot, bis=True)
    arrange_static(bot)
    add_interaction = interaction_factory()
    await invoke_registered(Member(bot), "add", add_interaction, add_interaction.user, "Player")
    await invoke_registered(
        Character(bot), "add", interaction_factory(), "Hero", "World", "MAIN", "PLD"
    )
    with bot.session_factory() as session:
        seed_reference_data(session)
        other = RaidTier(code="OTHER", name="Other Tier")
        pld = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
        session.add(BisSet(job=pld, raid_tier=other, name="Other Set"))
        static = session.scalar(select(Static))
        static.active_raid_tier = session.scalar(
            select(RaidTier).where(RaidTier.code == "FICTIONAL_ARC")
        )
        session.commit()
    interaction = interaction_factory()

    await invoke_registered(Bis(bot), "select", interaction, "Hero", "Other Set", None)

    with bot.session_factory() as session:
        assert session.scalar(select(CharacterBisSelection)) is None
    assert "Unknown BiS set for that tier" in interaction.messages[0]["content"]


@pytest.mark.parametrize(
    ("cog_type", "command", "args"),
    [
        (StaticCog, "create", ("Denied",)),
        (Member, "add", (SimpleNamespace(id=300), "Denied")),
        (Member, "deactivate", (SimpleNamespace(id=300),)),
        (Tier, "select", ("TIER",)),
        (Bis, "select", ("Name", "Set", None)),
        (Hierarchy, "set", ("PLD", False)),
    ],
)
async def test_write_command_rejects_user_without_role(
    bot, interaction_factory, cog_type, command, args
):
    interaction = interaction_factory(roles=())

    await invoke_registered(cog_type(bot), command, interaction, *args)

    assert interaction.messages[0]["content"] == ("You do not have permission to use this command.")


def test_every_write_command_has_effective_permission_check(bot):
    expected = {
        Setup: {"seed", "demo", "demo-refresh"},
        StaticCog: {"create", "edit", "deactivate", "reactivate"},
        Member: {"add", "edit", "deactivate", "reactivate"},
        Tier: {"import", "select", "clear"},
        Bis: {"import", "select", "clear"},
        Hierarchy: {"set"},
    }

    for cog_type, names in expected.items():
        cog = cog_type(bot)
        for name in names:
            assert registered_command(cog, name).checks, f"/{cog_type.__name__} {name}"


def test_every_advertised_callback_is_implemented_and_callable(bot):
    expected = {
        Setup: {"status", "seed", "demo", "demo-refresh"},
        StaticCog: {"create", "edit", "deactivate", "reactivate", "list", "show", "select"},
        Member: {"add", "edit", "list", "deactivate", "reactivate"},
        Character: {"add", "edit", "deactivate", "reactivate", "list"},
        Tier: {"import", "select", "clear", "show"},
        Bis: {"import", "select", "clear", "show"},
        Hierarchy: {"set", "show", "history"},
    }

    for cog_type, names in expected.items():
        cog = cog_type(bot)
        registered = {
            command.name: command
            for group in cog.__cog_app_commands__
            for command in group.commands
        }
        assert set(registered) == names
        assert all(callable(command.callback) for command in registered.values())


def test_current_gear_set_command_has_no_tier_or_item_level_options(bot):
    command = registered_command(Gear(bot), "set")
    assert [parameter.name for parameter in command.parameters] == ["display_name", "main_or_alt"]


def test_database_failure_rolls_back_and_closes_session():
    class TrackingSession:
        committed = rolled_back = closed = False

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    session = TrackingSession()
    bot = SimpleNamespace(session_factory=lambda: session)

    with pytest.raises(RuntimeError, match="database password=secret"), command_session(bot):
        raise RuntimeError("database password=secret")

    assert not session.committed
    assert session.rolled_back
    assert session.closed


async def test_deferred_interaction_uses_followup_without_double_response(bot):
    interaction = FakeInteraction(bot)
    await interaction.response.defer(ephemeral=True)

    await reply(interaction, "finished", ephemeral=True)

    assert interaction.response.messages == []
    assert interaction.followup.messages == [{"content": "finished", "ephemeral": True}]
    assert len(interaction.response.deferrals) == 1


def test_pagination_stays_within_discord_limits():
    result = pages(["x" * 190 for _ in range(25)])

    assert len(result) == 3
    assert all(len(page) <= 2000 for page in result)
    assert result[0].count("\n") == 9


async def test_unexpected_error_does_not_expose_credentials(bot):
    interaction = FakeInteraction(bot)
    secret = "postgresql://admin:token-value@db.internal/static"

    await handle_app_command_error(interaction, RuntimeError(secret))

    content = interaction.messages[0]["content"]
    assert content == "An unexpected internal error occurred."
    assert "token-value" not in content
    assert "db.internal" not in content
