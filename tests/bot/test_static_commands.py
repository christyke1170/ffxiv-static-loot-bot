from sqlalchemy import func, select

from app.models import Static, StaticMember, UserStaticPreference
from bot.commands.static import Static as StaticCog
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static


async def test_static_create_persists_and_replies(bot, interaction_factory):
    interaction = interaction_factory()

    await invoke_registered(StaticCog(bot), "create", interaction, "Progression")

    with bot.session_factory() as session:
        row = session.scalar(select(Static).where(Static.name == "Progression"))
        assert row is not None and row.guild.discord_guild_id == 100
    assert "Created static **Progression**" in interaction.messages[0]["content"]


async def test_duplicate_static_returns_user_safe_error(bot, interaction_factory):
    cog = StaticCog(bot)
    await invoke_registered(cog, "create", interaction_factory(), "Duplicate")
    interaction = interaction_factory()

    await invoke_registered(cog, "create", interaction, "Duplicate")

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Static)) == 1
    assert interaction.messages[0]["content"] == (
        "A static with that name already exists in this guild."
    )


async def test_static_list_excludes_other_guild(bot, interaction_factory):
    arrange_static(bot, guild_id=100, name="Visible")
    arrange_static(bot, guild_id=999, user_id=999, name="Secret")
    interaction = interaction_factory()

    await invoke_registered(StaticCog(bot), "list", interaction)

    content = "\n".join(message["content"] for message in interaction.messages)
    assert "Visible" in content
    assert "Secret" not in content


async def test_static_select_persists_preference(bot, interaction_factory):
    static_id = arrange_static(bot, selected=False)
    interaction = interaction_factory()

    await invoke_registered(StaticCog(bot), "select", interaction, static_id)

    with bot.session_factory() as session:
        preference = session.scalar(select(UserStaticPreference))
        assert preference.static_id == static_id
        assert preference.discord_user_id == interaction.user.id
    assert "Selected **Alpha**" in interaction.messages[0]["content"]


async def test_raid_leader_can_select_without_membership(bot, interaction_factory):
    static_id = arrange_static(bot, selected=False)
    interaction = interaction_factory()

    await invoke_registered(StaticCog(bot), "select", interaction, static_id)

    with bot.session_factory() as session:
        assert session.scalar(select(UserStaticPreference)).static_id == static_id


async def test_active_member_can_select_their_static(bot, interaction_factory):
    static_id = arrange_static(bot, selected=False)
    with bot.session_factory() as session:
        static = session.get(Static, static_id)
        session.add(StaticMember(static=static, discord_user_id=200, display_name="Member"))
        session.commit()
    interaction = interaction_factory(roles=())

    await invoke_registered(StaticCog(bot), "select", interaction, static_id)

    with bot.session_factory() as session:
        preference = session.scalar(select(UserStaticPreference))
        assert preference.static_id == static_id
        assert preference.discord_user_id == 200
    assert "Selected **Alpha**" in interaction.messages[0]["content"]


async def test_nonmember_cannot_select_static(bot, interaction_factory):
    static_id = arrange_static(bot, selected=False)
    interaction = interaction_factory(roles=())

    await invoke_registered(StaticCog(bot), "select", interaction, static_id)

    with bot.session_factory() as session:
        assert session.scalar(select(UserStaticPreference)) is None
    assert "active member of that static" in interaction.messages[0]["content"]


async def test_inactive_member_cannot_select_static(bot, interaction_factory):
    static_id = arrange_static(bot, selected=False)
    with bot.session_factory() as session:
        static = session.get(Static, static_id)
        session.add(
            StaticMember(
                static=static,
                discord_user_id=200,
                display_name="Former Member",
                active=False,
            )
        )
        session.commit()
    interaction = interaction_factory(roles=())

    await invoke_registered(StaticCog(bot), "select", interaction, static_id)

    with bot.session_factory() as session:
        assert session.scalar(select(UserStaticPreference)) is None
    assert "active member of that static" in interaction.messages[0]["content"]


async def test_bot_administrator_can_select_without_membership(bot, interaction_factory):
    static_id = arrange_static(bot, selected=False)
    interaction = interaction_factory(roles=(), administrator=True)

    await invoke_registered(StaticCog(bot), "select", interaction, static_id)

    with bot.session_factory() as session:
        assert session.scalar(select(UserStaticPreference)).static_id == static_id


async def test_cross_guild_static_selection_is_rejected(bot, interaction_factory):
    foreign_id = arrange_static(bot, guild_id=999, user_id=999, selected=False)
    interaction = interaction_factory(guild_id=100)

    await invoke_registered(StaticCog(bot), "select", interaction, foreign_id)

    with bot.session_factory() as session:
        assert session.scalar(select(UserStaticPreference)) is None
    assert "does not belong" in interaction.messages[0]["content"]


async def test_missing_selection_instructs_static_select(bot, interaction_factory):
    arrange_static(bot, selected=False)
    interaction = interaction_factory()

    await invoke_registered(StaticCog(bot), "show", interaction)

    assert "/static select" in interaction.messages[0]["content"]


async def test_static_show_callback_formats_selected_static(bot, interaction_factory):
    arrange_static(bot)
    interaction = interaction_factory()

    await invoke_registered(StaticCog(bot), "show", interaction)

    content = interaction.messages[0]["content"]
    assert "**Alpha**" in content
    assert "Members: 0" in content
