from sqlalchemy import func, select

from app.models import Character, Job, Static, StaticMember
from bot.commands.character import Character as CharacterCog
from bot.commands.member import Member
from tests.bot.fakes import FakeDiscordMember, invoke_registered
from tests.bot.helpers import arrange_static


async def test_member_add_persists_discord_user_id(bot, interaction_factory):
    arrange_static(bot)
    interaction = interaction_factory()
    target = FakeDiscordMember(id=3456)

    await invoke_registered(Member(bot), "add", interaction, target, "Player One")

    with bot.session_factory() as session:
        row = session.scalar(select(StaticMember))
        assert row.discord_user_id == 3456
        assert row.display_name == "Player One"
    assert "Added Player One" in interaction.messages[0]["content"]


async def test_member_deactivate_preserves_character_history(bot, interaction_factory):
    arrange_static(bot)
    interaction = interaction_factory(user_id=200)
    target = FakeDiscordMember(id=200)
    await invoke_registered(Member(bot), "add", interaction, target, "Historian")
    from app.services import seed_reference_data

    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()
    await invoke_registered(
        CharacterCog(bot), "add", interaction_factory(user_id=200), "Legacy", "World", "MAIN", "PLD"
    )

    deactivate_interaction = interaction_factory()
    await invoke_registered(Member(bot), "deactivate", deactivate_interaction, target)

    with bot.session_factory() as session:
        member = session.scalar(select(StaticMember))
        assert member.active is False
        assert [character.name for character in member.characters] == ["Legacy"]
        assert session.scalar(select(func.count()).select_from(Character)) == 1
    assert "history were retained" in deactivate_interaction.messages[0]["content"]


async def test_character_add_rejects_unknown_job(bot, interaction_factory):
    arrange_static(bot)
    interaction = interaction_factory()
    await invoke_registered(Member(bot), "add", interaction, interaction.user, "Player")
    command_interaction = interaction_factory()

    await invoke_registered(
        CharacterCog(bot), "add", command_interaction, "Hero", "World", "MAIN", "NOPE"
    )

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Character)) == 0
    assert "Unknown job abbreviation: NOPE" in command_interaction.messages[0]["content"]


async def test_active_member_can_add_own_main_and_alt(bot, interaction_factory):
    arrange_static(bot)
    leader = interaction_factory()
    await invoke_registered(Member(bot), "add", leader, leader.user, "Player")
    from app.services import seed_reference_data

    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()

    await invoke_registered(
        CharacterCog(bot),
        "add",
        interaction_factory(roles=()),
        "Main Hero",
        "World",
        "MAIN",
        "PLD",
    )
    await invoke_registered(
        CharacterCog(bot),
        "add",
        interaction_factory(roles=()),
        "Alt Hero",
        "World",
        "ALT",
        "WAR",
    )

    with bot.session_factory() as session:
        rows = list(session.scalars(select(Character).order_by(Character.name)))
        assert [(row.name, row.kind.value, row.static_member.discord_user_id) for row in rows] == [
            ("Alt Hero", "ALT", 200),
            ("Main Hero", "MAIN", 200),
        ]


async def test_user_cannot_add_character_under_another_member(bot, interaction_factory):
    arrange_static(bot)
    leader = interaction_factory()
    await invoke_registered(
        Member(bot), "add", leader, FakeDiscordMember(id=300), "Different Member"
    )
    command_interaction = interaction_factory(roles=())

    await invoke_registered(
        CharacterCog(bot), "add", command_interaction, "Wrong", "World", "MAIN", "PLD"
    )

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Character)) == 0
    assert "active member of the selected static" in command_interaction.messages[0]["content"]


async def test_inactive_member_cannot_add_character(bot, interaction_factory):
    arrange_static(bot)
    leader = interaction_factory()
    await invoke_registered(Member(bot), "add", leader, leader.user, "Former Member")
    await invoke_registered(Member(bot), "deactivate", interaction_factory(), leader.user)
    command_interaction = interaction_factory(roles=())

    await invoke_registered(
        CharacterCog(bot), "add", command_interaction, "Wrong", "World", "MAIN", "PLD"
    )

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Character)) == 0
    assert "active member of the selected static" in command_interaction.messages[0]["content"]


async def test_character_add_rejects_member_from_another_static(bot, interaction_factory):
    arrange_static(bot, guild_id=100, user_id=999, name="Selected")
    arrange_static(bot, guild_id=100, user_id=200, name="Other")
    interaction = interaction_factory(user_id=200)
    await invoke_registered(Member(bot), "add", interaction, interaction.user, "Other Member")
    # Move the user's persisted preference back to the static where they are not a member.
    first_static = 1
    await invoke_registered(
        __import__("bot.commands.static", fromlist=["Static"]).Static(bot),
        "select",
        interaction_factory(user_id=200),
        first_static,
    )
    command_interaction = interaction_factory(user_id=200)

    await invoke_registered(
        CharacterCog(bot), "add", command_interaction, "Wrong", "World", "MAIN", "PLD"
    )

    assert "active member of the selected static" in command_interaction.messages[0]["content"]


async def test_character_add_does_not_use_selection_from_another_guild(bot, interaction_factory):
    arrange_static(bot, guild_id=999, user_id=200, name="Foreign")
    with bot.session_factory() as session:
        foreign = session.scalar(select(Static).where(Static.name == "Foreign"))
        session.add(
            StaticMember(static=foreign, discord_user_id=200, display_name="Foreign Member")
        )
        session.commit()
    command_interaction = interaction_factory(guild_id=100, user_id=200, roles=())

    await invoke_registered(
        CharacterCog(bot), "add", command_interaction, "Wrong", "World", "MAIN", "PLD"
    )

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Character)) == 0
    assert "/static select" in command_interaction.messages[0]["content"]


async def test_raid_leader_role_allows_write_command(bot, interaction_factory):
    interaction = interaction_factory()

    await invoke_registered(
        __import__("bot.commands.static", fromlist=["Static"]).Static(bot),
        "create",
        interaction,
        "Leader Created",
    )

    with bot.session_factory() as session:
        assert session.scalar(select(StaticMember)) is None
        assert session.scalar(select(func.count()).select_from(Static)) == 1
    assert interaction.followup.messages


async def test_member_edit_and_reactivate_commands(bot, interaction_factory):
    arrange_static(bot)
    target = FakeDiscordMember(id=300)
    await invoke_registered(Member(bot), "add", interaction_factory(), target, "Before")
    await invoke_registered(Member(bot), "edit", interaction_factory(), target, "After")
    await invoke_registered(Member(bot), "deactivate", interaction_factory(), target)
    await invoke_registered(Member(bot), "reactivate", interaction_factory(), target)

    with bot.session_factory() as session:
        row = session.scalar(select(StaticMember).where(StaticMember.discord_user_id == 300))
        assert row.display_name == "After" and row.active


async def test_self_service_character_edit_and_lifecycle(bot, interaction_factory):
    arrange_static(bot)
    owner = interaction_factory()
    await invoke_registered(Member(bot), "add", owner, owner.user, "Owner")
    from app.services import seed_reference_data

    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()
    await invoke_registered(
        CharacterCog(bot), "add", interaction_factory(roles=()), "Self", "World", "MAIN", "PLD"
    )
    edited = interaction_factory(roles=())
    await invoke_registered(
        CharacterCog(bot),
        "edit",
        edited,
        "Self",
        None,
        "Self Corrected",
        "Other World",
        "ALT",
        "WAR",
        False,
    )
    await invoke_registered(
        CharacterCog(bot),
        "deactivate",
        interaction_factory(roles=()),
        "Self Corrected",
        None,
    )
    await invoke_registered(
        CharacterCog(bot),
        "reactivate",
        interaction_factory(roles=()),
        "Self Corrected",
        None,
    )

    with bot.session_factory() as session:
        row = session.scalar(select(Character))
        assert (row.name, row.world, row.kind.value, row.job.abbreviation, row.active) == (
            "Self Corrected",
            "Other World",
            "ALT",
            "WAR",
            True,
        )


async def test_leader_can_correct_another_member_but_nonleader_cannot(bot, interaction_factory):
    arrange_static(bot)
    target = FakeDiscordMember(id=300)
    await invoke_registered(Member(bot), "add", interaction_factory(), target, "Target")
    from app.services import seed_reference_data

    with bot.session_factory() as session:
        seed_reference_data(session)
        member = session.scalar(select(StaticMember).where(StaticMember.discord_user_id == 300))
        pld = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
        session.add(
            Character(
                static_member=member,
                job=pld,
                name="Target Character",
                world="World",
                kind="MAIN",
            )
        )
        session.commit()
    denied = interaction_factory(roles=())
    await invoke_registered(
        CharacterCog(bot),
        "edit",
        denied,
        "Target Character",
        target,
        "Denied",
        None,
        None,
        None,
        False,
    )
    assert "Raid-leader permission" in denied.messages[0]["content"]
    allowed = interaction_factory()
    await invoke_registered(
        CharacterCog(bot),
        "edit",
        allowed,
        "Target Character",
        target,
        "Corrected",
        None,
        None,
        None,
        False,
    )
    with bot.session_factory() as session:
        assert session.scalar(select(Character)).name == "Corrected"


async def test_character_correction_rejects_cross_static_and_inactive_member(
    bot, interaction_factory
):
    arrange_static(bot, name="Selected")
    arrange_static(bot, name="Other", user_id=999)
    target = FakeDiscordMember(id=300)
    await invoke_registered(Member(bot), "add", interaction_factory(user_id=999), target, "Other")
    cross = interaction_factory()
    await invoke_registered(
        CharacterCog(bot),
        "edit",
        cross,
        "Missing",
        target,
        "Nope",
        None,
        None,
        None,
        False,
    )
    assert "not in the selected static" in cross.messages[0]["content"]

    own = interaction_factory().user
    await invoke_registered(Member(bot), "add", interaction_factory(), own, "Former")
    from app.services import seed_reference_data

    with bot.session_factory() as session:
        seed_reference_data(session)
        member = session.scalar(select(StaticMember).where(StaticMember.discord_user_id == 200))
        job = session.scalar(select(Job))
        session.add(
            Character(
                static_member=member,
                job=job,
                name="Inactive Owner",
                world="World",
                kind="MAIN",
            )
        )
        member.active = False
        session.commit()
    inactive = interaction_factory(roles=())
    await invoke_registered(
        CharacterCog(bot),
        "edit",
        inactive,
        "Inactive Owner",
        None,
        "Nope",
        None,
        None,
        None,
        False,
    )
    assert "active static membership" in inactive.messages[0]["content"]
