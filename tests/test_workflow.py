from datetime import date

from sqlalchemy import select

from app.models import Character, CharacterKind, ClearMode, DiscordGuild, Job, Static, StaticMember
from app.services.hierarchy import ensure_default_hierarchy
from app.services.reclear import create_reclear_week
from app.services.seed import seed_reference_data


def test_neutral_week_has_four_fixed_floors(session):
    seed_reference_data(session)
    s = Static(guild=DiscordGuild(discord_guild_id=1001, name="G"), name="S", active=True)
    session.flush()
    ensure_default_hierarchy(session, s)
    job = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    for index in range(8):
        member = StaticMember(static=s, discord_user_id=1100 + index, display_name=f"P{index}")
        session.add(
            Character(
                static_member=member,
                job=job,
                name=f"C{index}",
                world="W",
                kind=CharacterKind.MAIN,
            )
        )
    session.flush()
    week = create_reclear_week(session, s, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    assert [row.floor_number for row in week.neutral_floors] == [1, 2, 3, 4]
