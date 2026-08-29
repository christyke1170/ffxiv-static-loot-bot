import discord
from discord import app_commands
from discord.ext import commands

from bot.checks import require_bot_admin, require_raid_leader
from bot.services.admin import (
    add_member,
    deactivate_member,
    delete_member,
    edit_member,
    reactivate_member,
)
from bot.services.commands import command_session, defer, pages, reply, selected
from bot.views.deletion import DeleteConfirmationView


class Member(commands.Cog):
    group = app_commands.Group(name="member", description="Manage static members")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="add")
    @require_raid_leader(None)
    async def add(self, interaction, member: discord.Member, display_name: str):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            row = add_member(session, selected(session, interaction), member.id, display_name)
        await reply(
            interaction, f"Added {discord.utils.escape_markdown(row.display_name)}.", ephemeral=True
        )

    @group.command(name="list")
    async def list(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            rows = [
                f"{m.display_name} - {'active' if m.active else 'inactive'} - "
                + ", ".join(f"{c.name}@{c.world} ({c.job.abbreviation})" for c in m.characters)
                for m in static.members
            ]
        for page in pages([discord.utils.escape_markdown(x) for x in rows]):
            await reply(interaction, page)

    @group.command(name="deactivate")
    @require_raid_leader(None)
    async def deactivate(self, interaction, member: discord.Member):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            deactivate_member(
                session, selected(session, interaction), member.id, interaction.user.id
            )
        await reply(
            interaction, "Member deactivated; characters and history were retained.", ephemeral=True
        )

    @group.command(name="edit", description="Correct a static member's display name")
    @require_raid_leader(None)
    async def edit(self, interaction, member: discord.Member, display_name: str):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            row = edit_member(
                session,
                selected(session, interaction),
                member.id,
                display_name,
                interaction.user.id,
            )
        await reply(
            interaction,
            f"Member display name changed to {discord.utils.escape_markdown(row.display_name)}.",
            ephemeral=True,
        )

    @group.command(name="reactivate", description="Reactivate a member without losing history")
    @require_raid_leader(None)
    async def reactivate(self, interaction, member: discord.Member):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            row = reactivate_member(
                session, selected(session, interaction), member.id, interaction.user.id
            )
        await reply(
            interaction,
            f"{discord.utils.escape_markdown(row.display_name)} reactivated; characters and "
            "history were retained.",
            ephemeral=True,
        )

    @group.command(name="delete", description="Permanently delete a static member")
    @require_bot_admin(None)
    async def delete(self, interaction, member: discord.Member):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = next((row for row in static.members if row.discord_user_id == member.id), None)
            if target is None:
                raise ValueError("That Discord member is not in the selected static.")
            display_name = target.display_name
            static_id = static.id

        async def confirm(callback_interaction):
            with command_session(self.bot) as session:
                target_static = session.get(type(static), static_id)
                if target_static is None:
                    raise ValueError("The selected static no longer exists.")
                delete_member(session, target_static, member.id)
            return f"Member **{discord.utils.escape_markdown(display_name)}** permanently deleted."

        view = DeleteConfirmationView(confirm, f"Permanently delete member **{display_name}**?")
        await interaction.followup.send(view.prompt, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Member(bot))
