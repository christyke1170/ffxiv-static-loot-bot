"""Small, Discord-independent helpers used by administrative commands."""

import json
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import DiscordGuild, UserStaticPreference

MAX_ATTACHMENT_BYTES = 1024 * 1024
PAGE_SIZE = 10


@contextmanager
def command_session(bot: Any):
    factory: sessionmaker[Session] = bot.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def guild_context(interaction: Any) -> tuple[int, int]:
    if interaction.guild is None:
        raise ValueError("This command can only be used in a Discord guild.")
    return interaction.guild.id, interaction.user.id


def selected(session: Session, interaction: Any):
    guild_id, user_id = guild_context(interaction)
    row = session.scalar(
        select(UserStaticPreference)
        .join(DiscordGuild, UserStaticPreference.guild_id == DiscordGuild.id)
        .where(
            DiscordGuild.discord_guild_id == guild_id,
            UserStaticPreference.discord_user_id == user_id,
        )
    )
    if row is None or row.static.guild.discord_guild_id != guild_id:
        raise ValueError("Select a static first with `/static select`.")
    return row.static


async def defer(interaction: Any, *, ephemeral: bool = False) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)


async def reply(interaction: Any, content: str, *, ephemeral: bool = False) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content[:2000], ephemeral=ephemeral)
    else:
        await interaction.response.send_message(content[:2000], ephemeral=ephemeral)


def pages(lines: list[str], size: int = PAGE_SIZE) -> list[str]:
    return ["\n".join(lines[index : index + size]) for index in range(0, len(lines), size)] or [
        "No records found."
    ]


async def read_json_attachment(attachment: Any) -> dict[str, Any]:
    filename = getattr(attachment, "filename", "")
    if not filename.lower().endswith(".json"):
        raise ValueError("The attachment filename must end with `.json`.")
    size = getattr(attachment, "size", None)
    if size is not None and size > MAX_ATTACHMENT_BYTES:
        raise ValueError("The attachment must be no larger than 1 MiB.")
    raw = await attachment.read()
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise ValueError("The attachment must be no larger than 1 MiB.")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("The attachment is not valid UTF-8.") from exc
    try:
        data = json.loads(
            text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
        )
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("The attachment does not contain valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("The attachment JSON must contain an object.")
    return data
