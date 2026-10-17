"""Idempotent application-owned emoji registration using bundled FFXIV icons."""

import logging
from importlib.resources import files

import discord

from app.domain.jobs import JOB_CATALOG
from app.services.job_labels import job_label

log = logging.getLogger(__name__)


def job_emoji(bot, abbreviation):
    return getattr(bot, "job_emojis", {}).get((abbreviation or "").upper())


def display_job(bot, abbreviation):
    return job_label(abbreviation, getattr(bot, "job_emojis", {}))


async def sync_job_emojis(client):
    """Reuse this application's emojis; create only missing bundled job icons."""
    client.job_emojis = {}
    if client.application_id is None:
        return
    try:
        existing = {emoji.name: emoji for emoji in await client.fetch_application_emojis()}
    except discord.HTTPException as error:
        log.warning(
            "Application job emojis unavailable (HTTP %s); using text labels.", error.status
        )
        return
    for job in JOB_CATALOG:
        name = f"job_{job.abbreviation.lower()}"
        emoji = existing.get(name)
        if emoji is None:
            asset = files("bot").joinpath("assets", "jobs", f"{job.abbreviation.lower()}.png")
            if not asset.is_file():
                log.warning("No bundled icon for %s; using its text label.", job.abbreviation)
                continue
            data = asset.read_bytes()
            if len(data) > 256 * 1024 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                log.warning("Invalid bundled icon for %s; using its text label.", job.abbreviation)
                continue
            try:
                emoji = await client.create_application_emoji(name=name, image=data)
            except discord.HTTPException as error:
                log.warning(
                    "Job emoji %s unavailable (HTTP %s); using text.",
                    job.abbreviation,
                    error.status,
                )
                continue
        client.job_emojis[job.abbreviation] = discord.PartialEmoji(
            name=emoji.name,
            id=emoji.id,
            animated=emoji.animated,
        )
    log.info("Application job icons ready: %s/%s", len(client.job_emojis), len(JOB_CATALOG))
