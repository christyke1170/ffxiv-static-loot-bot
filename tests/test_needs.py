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
from app.services.needs_v2 import calculate_character_needs_v2


def test_static_job_bis_drives_neutral_needs(session):
    from app.services.seed import seed_reference_data

    seed_reference_data(session)
    j = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    s = Static(guild=DiscordGuild(discord_guild_id=901, name="G"), name="S")
    m = StaticMember(static=s, discord_user_id=902, display_name="P")
    c = Character(static_member=m, job=j, name="C", world="W", kind=CharacterKind.MAIN)
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
    result = calculate_character_needs_v2(session, c.id)
    assert result.bis_set_id == b.id and len(result.slot_results) == 12
