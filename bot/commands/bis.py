from discord import app_commands
from discord.ext import commands

from app.models import GearSlot
from bot.checks import require_raid_leader
from bot.services.bis import resolve_job, summarize_bis
from bot.services.commands import command_session, defer, reply, selected
from bot.views.bis import BisClearView, BisEditorView


class Bis(commands.Cog):
    group = app_commands.Group(name="bis", description="Manage BiS sets")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="set", description="Configure category-only BiS for a job")
    @require_raid_leader(None)
    async def set(self, interaction, job: str):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            job_row = resolve_job(session, job)
            view = BisEditorView(
                self.bot, static.id, job_row.id, interaction.user.id, interaction.guild.id
            )
        await interaction.followup.send(view=view, ephemeral=True)

    @group.command(name="show", description="Show category-only BiS for a job")
    @require_raid_leader(None)
    async def show(self, interaction, job: str):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            job_row = resolve_job(session, job)
            summary = summarize_bis(session, static, job_row)
            if summary.bis_set is None:
                text = (
                    f"No BiS is configured for {job_row.abbreviation} in {static.name}. "
                    "Use /bis set."
                )
            else:
                items = {
                    item.gear_slot.display_name: item.classification.value
                    for item in summary.bis_set.items
                }
                slot_count = session.query(GearSlot).count()
                complete = len(items) == slot_count
                lines = [
                    f"Static: {static.name}",
                    f"Job: {job_row.abbreviation}",
                    f"Offhand applicable: {'yes' if job_row.uses_offhand else 'no'}",
                    f"Complete: {'yes' if complete else 'no'}",
                    f"Active Mains: {summary.main_count}",
                    f"Active Alts: {summary.alt_count}",
                ]
                lines.extend(f"{slot}: {category}" for slot, category in items.items())
                text = "\n".join(lines)
        await reply(interaction, text, ephemeral=True)

    @group.command(name="clear", description="Clear category-only BiS for a job")
    @require_raid_leader(None)
    async def clear(self, interaction, job: str):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            job_row = resolve_job(session, job)
            if summarize_bis(session, static, job_row).bis_set is None:
                raise ValueError(
                    f"No BiS is configured for {job_row.abbreviation} in {static.name}."
                )
            view = BisClearView(
                self.bot, static.id, job_row.id, interaction.user.id, interaction.guild.id
            )
        await interaction.followup.send(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Bis(bot))
