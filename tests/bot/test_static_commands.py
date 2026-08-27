import pytest
from sqlalchemy import func, select

from app.models import AuditLog, CharacterGearSlot, Static, StaticMember, UserStaticPreference
from bot.commands.static import Static as StaticCog
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static


async def test_static_create_persists_and_replies(bot, interaction_factory):
    interaction = interaction_factory()

    await invoke_registered(StaticCog(bot), "create", interaction, "Progression", 710)

    with bot.session_factory() as session:
        row = session.scalar(select(Static).where(Static.name == "Progression"))
        assert row is not None and row.guild.discord_guild_id == 100
        assert row.crafted_item_level == 710
    assert "Created static **Progression**" in interaction.messages[0]["content"]


@pytest.mark.parametrize("value", [0, -1])
async def test_static_create_rejects_nonpositive_baseline(bot, interaction_factory, value):
    interaction = interaction_factory()
    await invoke_registered(StaticCog(bot), "create", interaction, "Invalid", value)
    with bot.session_factory() as session:
        assert session.scalar(select(Static)) is None
    assert "positive integer" in interaction.messages[0]["content"]


async def test_item_level_command_audits_once_without_rewriting_gear(bot, interaction_factory):
    static_id = arrange_static(bot)
    with bot.session_factory() as session:
        static = session.get(Static, static_id)
        static.crafted_item_level = None
        session.commit()
        before = session.scalar(select(func.count()).select_from(CharacterGearSlot))
    interaction = interaction_factory()
    await invoke_registered(StaticCog(bot), "item-level", interaction, 710)
    with bot.session_factory() as session:
        static = session.get(Static, static_id)
        assert static.crafted_item_level == 710
        assert session.scalar(select(func.count()).select_from(CharacterGearSlot)) == before
        audits = list(
            session.scalars(
                select(AuditLog).where(AuditLog.action == "STATIC_CRAFTED_ITEM_LEVEL_CHANGED")
            )
        )
        assert len(audits) == 1
    assert "not configured" in interaction.messages[0]["content"]


async def test_item_level_command_requires_permission(bot, interaction_factory):
    static_id = arrange_static(bot)
    interaction = interaction_factory(roles=())
    await invoke_registered(StaticCog(bot), "item-level", interaction, 710)
    with bot.session_factory() as session:
        assert session.get(Static, static_id).crafted_item_level is None


async def test_duplicate_static_returns_user_safe_error(bot, interaction_factory):
    cog = StaticCog(bot)
    await invoke_registered(cog, "create", interaction_factory(), "Duplicate", 710)
    interaction = interaction_factory()

    await invoke_registered(cog, "create", interaction, "Duplicate", 710)

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
