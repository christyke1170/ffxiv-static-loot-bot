"""Database arrangement helpers; command behavior is never implemented here."""

from sqlalchemy import select

from app.models import DiscordGuild, Static, UserStaticPreference
from app.services import seed_reference_data


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
    """Seed neutral reference data for command tests.

    The old tier/BiS JSON import arrangement was retired with the configurable
    tier graph; Static + Job BiS tests arrange rows directly.
    """
    with bot.session_factory() as session:
        seed_reference_data(session)
        session.commit()
