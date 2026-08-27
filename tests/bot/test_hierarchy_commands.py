from sqlalchemy import func, select

from app.models import (
    BisSet,
    Character,
    CharacterBisSelection,
    CharacterKind,
    Job,
    JobHierarchy,
    RaidTier,
    Static,
    StaticMember,
)
from app.services import seed_reference_data
from bot.commands.hierarchy import Hierarchy
from tests.bot.fakes import invoke_registered
from tests.bot.helpers import arrange_static


async def test_hierarchy_set_creates_and_activates_version(bot, interaction_factory):
    arrange_static(bot)
    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()
    interaction = interaction_factory()

    await invoke_registered(Hierarchy(bot), "set", interaction, "PLD, WHM", False)

    with bot.session_factory() as session:
        row = session.scalar(select(JobHierarchy))
        assert row.version == 1 and row.active
        assert [entry.job.abbreviation for entry in row.entries] == ["PLD", "WHM"]
    assert "version 1 activated" in interaction.messages[0]["content"]


async def test_hierarchy_new_version_deactivates_previous(bot, interaction_factory):
    arrange_static(bot)
    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()
    cog = Hierarchy(bot)
    await invoke_registered(cog, "set", interaction_factory(), "PLD", False)

    await invoke_registered(cog, "set", interaction_factory(), "WAR", False)

    with bot.session_factory() as session:
        rows = session.scalars(select(JobHierarchy).order_by(JobHierarchy.version)).all()
        assert [(row.version, row.active) for row in rows] == [(1, False), (2, True)]


async def test_duplicate_hierarchy_jobs_are_rejected(bot, interaction_factory):
    arrange_static(bot)
    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()
    interaction = interaction_factory()

    await invoke_registered(Hierarchy(bot), "set", interaction, "PLD, PLD", False)

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JobHierarchy)) == 0
    assert "non-empty and unique" in interaction.messages[0]["content"]


async def test_unknown_hierarchy_job_is_rejected(bot, interaction_factory):
    arrange_static(bot)
    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()
    interaction = interaction_factory()

    await invoke_registered(Hierarchy(bot), "set", interaction, "PLD, NOPE", False)

    assert "Unknown jobs: NOPE" in interaction.messages[0]["content"]


async def test_missing_active_main_job_requires_force(bot, interaction_factory):
    static_id = arrange_static(bot)
    with bot.session_factory() as session:
        seed_reference_data(session)
        static = session.get(Static, static_id)
        tier = RaidTier(code="CURRENT", name="Current")
        static.active_raid_tier = tier
        pld = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
        member = StaticMember(static=static, discord_user_id=200, display_name="Main")
        character = Character(
            static_member=member,
            job=pld,
            name="Main Character",
            world="World",
            kind=CharacterKind.MAIN,
        )
        bis = BisSet(job=pld, raid_tier=tier, name="Main Set")
        session.add(CharacterBisSelection(character=character, raid_tier=tier, bis_set=bis))
        session.commit()
    rejected = interaction_factory()

    await invoke_registered(Hierarchy(bot), "set", rejected, "WAR", False)

    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JobHierarchy)) == 0
    assert "missing active main jobs: PLD" in rejected.messages[0]["content"]

    forced = interaction_factory()
    await invoke_registered(Hierarchy(bot), "set", forced, "WAR", True)
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JobHierarchy)) == 1
    assert "activated" in forced.messages[0]["content"]
