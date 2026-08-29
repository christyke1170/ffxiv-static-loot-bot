from sqlalchemy import select

from app.models import (
    BisSet,
    BisSetItem,
    Character,
    CharacterKind,
    DiscordGuild,
    GearClassification,
    GearSlot,
    Job,
    Static,
    StaticMember,
)
from app.services.board import build_static_gear_board
from app.services.seed import seed_reference_data


def test_board_contains_mains_and_category_state(session):
    seed_reference_data(session)
    g = DiscordGuild(discord_guild_id=501, name="G")
    j = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    s = Static(guild=g, name="S")
    m = StaticMember(static=s, discord_user_id=502, display_name="P")
    c = Character(static_member=m, job=j, name="Main", world="W", kind=CharacterKind.MAIN)
    b = BisSet(static=s, job=j, name="PLD")
    session.add_all([c, b])
    session.flush()
    session.add_all(
        [
            BisSetItem(bis_set=b, gear_slot=x, classification=GearClassification.CRAFTED_EX)
            for x in session.scalars(select(GearSlot))
        ]
    )
    session.commit()
    board = build_static_gear_board(session, s.id)
    assert len(board.players) == 1 and board.players[0].character_kind is CharacterKind.MAIN
