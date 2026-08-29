import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from bot.checks import is_raid_leader, require_raid_leader
from bot.services.admin import (
    create_static,
    deactivate_static,
    edit_static,
    guild,
    list_statics,
    reactivate_static,
    resolve_static,
    select_static,
)
from bot.services.commands import command_session, defer, guild_context, pages, reply, selected


class Static(commands.Cog):
    group = app_commands.Group(name="static", description="Manage statics")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="create")
    @require_raid_leader(None)
    async def create(self, interaction, name: str, crafted_item_level: int):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            guild_row = guild(session, interaction.guild.id, interaction.guild.name)
            row = create_static(session, guild_row.id, name, crafted_item_level)
        await reply(
            interaction,
            f"Created static **{discord.utils.escape_markdown(row.name)}**.",
            ephemeral=True,
        )

    @group.command(name="edit", description="Rename the selected static without changing history")
    @require_raid_leader(None)
    async def edit(self, interaction, new_name: str):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            row = selected(session, interaction)
            old = row.name
            edit_static(session, row, new_name, interaction.user.id)
        await reply(
            interaction,
            f"Renamed **{discord.utils.escape_markdown(old)}** to "
            f"**{discord.utils.escape_markdown(row.name)}**; history was retained.",
            ephemeral=True,
        )

    @group.command(name="deactivate", description="Deactivate the selected static safely")
    @require_raid_leader(None)
    async def deactivate(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            row = deactivate_static(session, selected(session, interaction), interaction.user.id)
        await reply(
            interaction,
            f"Static **{discord.utils.escape_markdown(row.name)}** deactivated; "
            "all history was retained.",
            ephemeral=True,
        )

    @group.command(name="reactivate", description="Reactivate the selected static")
    @require_raid_leader(None)
    async def reactivate(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            row = reactivate_static(session, selected(session, interaction), interaction.user.id)
        await reply(
            interaction,
            f"Static **{discord.utils.escape_markdown(row.name)}** reactivated.",
            ephemeral=True,
        )

    @group.command(name="list")
    async def list(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            rows = list_statics(session, interaction.guild.id)
        for page in pages(
            [
                f"{row.id}: {discord.utils.escape_markdown(row.name)} "
                f"({'active' if row.active else 'inactive'})"
                for row in rows
            ]
        ):
            await reply(interaction, page)

    @group.command(name="show")
    async def show(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            row = __import__("bot.services.admin", fromlist=["selected_static"]).selected_static(
                session, *guild_context(interaction)
            )
            hierarchy = row.job_hierarchies and next(
                (h for h in row.job_hierarchies if h.active), None
            )
            jobs = ", ".join(e.job.abbreviation for e in hierarchy.entries) if hierarchy else "none"
            weeks = len(row.reclear_weeks)
            text = "\n".join(
                (
                    f"**{discord.utils.escape_markdown(row.name)}**",
                    f"Active: {row.active}",
                    f"Crafted item level: {row.crafted_item_level or 'not configured'}",
                    f"Members: {len(row.members)}",
                    f"Hierarchy: {jobs}",
                    f"Reclear weeks: {weeks}",
                )
            )
        await reply(interaction, text)

    @group.command(name="item-level", description="Set the selected static's crafted baseline")
    @require_raid_leader(None)
    async def item_level(self, interaction, value: int):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            row = selected(session, interaction)
            from bot.services.admin import set_crafted_item_level

            previous, current = set_crafted_item_level(session, row, value, interaction.user.id)
        await reply(
            interaction,
            f"Crafted item level changed from **{previous or 'not configured'}** to **{current}**.",
            ephemeral=True,
        )

    @group.command(name="select")
    async def select(self, interaction, static_id: int):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            guild_id, user_id = guild_context(interaction)
            row = resolve_static(session, guild_id, static_id)
            if not is_raid_leader(interaction, None) and not any(
                member.active and member.discord_user_id == user_id for member in row.members
            ):
                raise ValueError("You must be an active member of that static to select it.")
            guild_row = session.scalar(
                select(type(row.guild)).where(type(row.guild).discord_guild_id == guild_id)
            )
            select_static(session, guild_row.id, user_id, row)
        await reply(
            interaction, f"Selected **{discord.utils.escape_markdown(row.name)}**.", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Static(bot))
