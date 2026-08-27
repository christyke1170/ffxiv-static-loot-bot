from sqlalchemy import func, select

from app.models import GearSlot, Job, Static
from bot.commands.setup import Setup
from tests.bot.conftest import BOT_ADMIN_ROLE
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static


async def test_setup_seed_calls_real_service_and_changes_database(bot, interaction_factory):
    interaction = interaction_factory(roles=(BOT_ADMIN_ROLE,))

    await invoke_registered(Setup(bot), "seed", interaction)

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 21
        assert session.scalar(select(func.count()).select_from(GearSlot)) == 12
    assert "21 inserted" in interaction.messages[0]["content"]


async def test_setup_seed_is_idempotent(bot, interaction_factory):
    cog = Setup(bot)
    first = interaction_factory(roles=(BOT_ADMIN_ROLE,))
    second = interaction_factory(roles=(BOT_ADMIN_ROLE,))

    await invoke_registered(cog, "seed", first)
    await invoke_registered(cog, "seed", second)

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 21
        assert session.scalar(select(func.count()).select_from(GearSlot)) == 12
    assert "21 existing" in second.messages[0]["content"]
    assert "12 existing" in second.messages[0]["content"]


async def test_setup_status_returns_database_backed_values(bot, interaction_factory):
    arrange_static(bot, name="One")
    arrange_static(bot, name="Two", selected=False)
    seed_interaction = interaction_factory(roles=(BOT_ADMIN_ROLE,))
    await invoke_registered(Setup(bot), "seed", seed_interaction)
    interaction = interaction_factory(roles=())

    await invoke_registered(Setup(bot), "status", interaction)

    content = interaction.messages[0]["content"]
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Static)) == 2
    assert "jobs 21/21" in content
    assert "gear slots 12/12" in content
    assert "Statics: 2" in content


async def test_bot_admin_role_allows_setup_seed(bot, interaction_factory):
    interaction = interaction_factory(roles=(BOT_ADMIN_ROLE,))

    await invoke_registered(Setup(bot), "seed", interaction)

    assert interaction.response.deferrals == [{"ephemeral": True}]
    assert interaction.followup.messages[0]["content"].startswith("Seed complete")


async def test_setup_seed_rejects_non_admin_before_write(bot, interaction_factory):
    interaction = interaction_factory(roles=())

    await invoke_registered(Setup(bot), "seed", interaction)

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0
    assert interaction.messages == [
        {"content": "You do not have permission to use this command.", "ephemeral": True}
    ]
