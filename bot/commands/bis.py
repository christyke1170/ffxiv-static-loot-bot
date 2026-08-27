import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import BisSet, Character, CharacterBisSelection, RaidTier
from app.services.imports import import_bis_sets
from bot.checks import require_raid_leader
from bot.services.admin import clear_bis, select_bis
from bot.services.commands import (
    command_session,
    defer,
    read_json_attachment,
    reply,
    selected,
)


class Bis(commands.Cog):
    group = app_commands.Group(name="bis", description="Manage BiS sets")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="import")
    @require_raid_leader(None)
    async def import_bis(self, interaction, attachment: discord.Attachment):
        await defer(interaction, ephemeral=True)
        data = await read_json_attachment(attachment)
        with command_session(self.bot) as session:
            import_bis_sets(session, data, dry_run=True)
        with command_session(self.bot) as session:
            rows = import_bis_sets(session, data)
            count = sum(len(row.items) for row in rows)
            counts = rows.counts
        await reply(
            interaction,
            f"Imported {len(rows)} BiS sets and {count} items. "
            f"Counts: inserted {counts.inserted}, updated {counts.updated}, "
            f"unchanged {counts.unchanged}, rejected {counts.rejected}; "
            f"{len(rows)} accepted set(s), {count} item(s)."
            + (" Referenced definitions were retained unchanged." if counts.rejected else ""),
            ephemeral=True,
        )

    @group.command(name="select")
    @require_raid_leader(None)
    async def select(self, interaction, character: str, bis_set: str, tier: str | None = None):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            member_ids = [member.id for member in static.members]
            character_row = session.scalar(
                select(Character).where(
                    Character.name == character, Character.static_member_id.in_(member_ids)
                )
            )
            if character_row is None:
                raise ValueError("Character is not in the selected static.")
            tier_row = (
                static.active_raid_tier
                if tier is None
                else session.scalar(
                    select(RaidTier).where((RaidTier.code == tier) | (RaidTier.name == tier))
                )
            )
            if tier_row is None:
                raise ValueError("Unknown raid tier.")
            set_row = session.scalar(
                select(BisSet).where(BisSet.raid_tier_id == tier_row.id, BisSet.name == bis_set)
            )
            if set_row is None:
                raise ValueError("Unknown BiS set for that tier.")
            change = select_bis(session, character_row, tier_row, set_row)
            old = change.old.name if change.old else "none"
            status = "unchanged" if not change.changed else "replaced"
        await reply(
            interaction,
            f"BiS selection {status}: {discord.utils.escape_markdown(old)} → "
            f"{discord.utils.escape_markdown(set_row.name)}.",
            ephemeral=True,
        )

    @group.command(name="clear", description="Clear a BiS selection when no workflow depends on it")
    @require_raid_leader(None)
    async def clear(self, interaction, character: str, tier: str | None = None):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            member_ids = [member.id for member in static.members]
            character_row = session.scalar(
                select(Character).where(
                    Character.name == character, Character.static_member_id.in_(member_ids)
                )
            )
            if character_row is None:
                raise ValueError("Character is not in the selected static.")
            tier_row = (
                static.active_raid_tier
                if tier is None
                else session.scalar(
                    select(RaidTier).where((RaidTier.code == tier) | (RaidTier.name == tier))
                )
            )
            if tier_row is None:
                raise ValueError("Unknown raid tier.")
            change = clear_bis(session, static, character_row, tier_row)
            old = change.old.name if change.old else "none"
        await reply(
            interaction,
            f"BiS selection {'cleared' if change.changed else 'unchanged'}: "
            f"{discord.utils.escape_markdown(old)} → none.",
            ephemeral=True,
        )

    @group.command(name="show")
    async def show(self, interaction, character: str):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            row = session.scalar(
                select(CharacterBisSelection).where(
                    CharacterBisSelection.character.has(
                        (Character.name == character)
                        & Character.static_member_id.in_([member.id for member in static.members])
                    )
                )
            )
            if row is None:
                raise ValueError("No BiS selection found.")
            text = "\n".join(
                [
                    f"Character: {row.character.name}",
                    f"Job: {row.character.job.abbreviation}",
                    f"Set: {row.bis_set.name}",
                    f"GCD: {row.bis_set.gcd_label or 'none'}",
                    f"Link: {row.bis_set.gear_set_url or 'none'}",
                    f"Desired slots: {len(row.bis_set.items)}",
                ]
            )
        await reply(interaction, text)


async def setup(bot):
    await bot.add_cog(Bis(bot))
