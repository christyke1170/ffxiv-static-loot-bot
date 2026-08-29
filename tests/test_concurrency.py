from sqlalchemy import select

from app.models import (
    Character,
    CharacterKind,
    DiscordGuild,
    Job,
    Static,
    StaticMember,
    V2ResourceBalance,
)
from app.services.neutral_resources import set_current_balance


def test_neutral_balance_update_is_idempotent(session):
    j = Job(abbreviation="TST", name="Test", role="Test")
    s = Static(guild=DiscordGuild(discord_guild_id=601, name="G"), name="S")
    m = StaticMember(static=s, discord_user_id=602, display_name="P")
    c = Character(static_member=m, job=j, name="C", world="W", kind=CharacterKind.MAIN)
    session.add(c)
    session.flush()
    set_current_balance(session, s, c, "BOOK_FLOOR_1", 2)
    set_current_balance(session, s, c, "BOOK_FLOOR_1", 2)
    session.commit()
    assert (
        session.scalar(select(V2ResourceBalance).where(V2ResourceBalance.quantity == 2)) is not None
    )
