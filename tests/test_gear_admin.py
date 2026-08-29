from sqlalchemy import select

from app.models import (
    Character,
    CharacterKind,
    DiscordGuild,
    GearClassification,
    GearSlot,
    Job,
    Static,
    StaticMember,
    V2ResourceBalance,
)
from app.services.gear import set_gear
from app.services.neutral_resources import set_current_balance


def test_gear_and_neutral_resources_are_separate(session):
    from app.services.seed import seed_reference_data

    seed_reference_data(session)
    j = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    s = Static(guild=DiscordGuild(discord_guild_id=801, name="G"), name="S")
    m = StaticMember(static=s, discord_user_id=802, display_name="P")
    c = Character(static_member=m, job=j, name="C", world="W", kind=CharacterKind.MAIN)
    session.add(c)
    session.flush()
    slot = session.scalar(select(GearSlot).where(GearSlot.code == "HEAD"))
    row = set_gear(session, s, c, slot, GearClassification.SAVAGE, 99)
    set_current_balance(session, s, c, "HEAD_COFFER", 1)
    session.commit()
    assert (
        row.current_classification is GearClassification.SAVAGE
        and session.scalar(
            select(V2ResourceBalance).where(V2ResourceBalance.resource_key == "HEAD_COFFER")
        ).quantity
        == 1
    )
