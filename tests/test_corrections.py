from sqlalchemy import select

from app.models import Character, CharacterKind, DiscordGuild, Job, Static, StaticMember
from app.services.seed import seed_reference_data
from bot.services.admin import deactivate_static, edit_static, reactivate_static, set_hierarchy


def make_state(session):
    seed_reference_data(session)
    s = Static(guild=DiscordGuild(discord_guild_id=701, name="G"), name="Original")
    m = StaticMember(static=s, discord_user_id=702, display_name="P")
    j = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    session.add(Character(static_member=m, job=j, name="C", world="W", kind=CharacterKind.MAIN))
    session.commit()
    return s


def test_static_lifecycle_preserves_identity(session):
    s = make_state(session)
    old = s.id
    edit_static(session, s, "Renamed", 99)
    deactivate_static(session, s, 99)
    reactivate_static(session, s, 99)
    assert (s.id, s.name, s.active) == (old, "Renamed", True)


def test_hierarchy_reorder_is_versioned(session):
    s = make_state(session)
    first = set_hierarchy(session, s, "PLD,WAR", True)
    second = set_hierarchy(session, s, "WAR,PLD", True)
    assert first.version == 1 and second.version == 2 and not first.active
