"""Database arrangement helpers; command behavior is never implemented here."""

import json
from pathlib import Path

from sqlalchemy import select

from app.models import DiscordGuild, Static, UserStaticPreference
from app.services import import_bis_sets, import_raid_tier, seed_reference_data

ROOT = Path(__file__).parents[2]
TIER_DATA = json.loads((ROOT / "sample_data" / "fictional_raid_tier.json").read_text("utf-8"))
BIS_DATA = json.loads((ROOT / "sample_data" / "fictional_bis_sets.json").read_text("utf-8"))


def arrange_static(
    bot,
    *,
    guild_id: int = 100,
    user_id: int = 200,
    name: str = "Alpha",
    selected: bool = True,
) -> int:
    with bot.session_factory() as session:
        guild = session.scalar(
            select(DiscordGuild).where(DiscordGuild.discord_guild_id == guild_id)
        )
        if guild is None:
            guild = DiscordGuild(discord_guild_id=guild_id, name=f"Guild {guild_id}")
        static = Static(guild=guild, name=name)
        session.add(static)
        session.flush()
        if selected:
            session.add(
                UserStaticPreference(
                    guild_id=guild.id, discord_user_id=user_id, static_id=static.id
                )
            )
        session.commit()
        return static.id


def arrange_imports(bot, *, bis: bool = False) -> None:
    with bot.session_factory() as session:
        seed_reference_data(session)
        import_raid_tier(session, TIER_DATA)
        if bis:
            import_bis_sets(session, BIS_DATA)
        session.commit()
