from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import JobHierarchy
from bot.checks import require_raid_leader
from bot.services.admin import set_hierarchy
from bot.services.commands import command_session, defer, pages, reply, selected


class Hierarchy(commands.Cog):
    group = app_commands.Group(name="hierarchy", description="Manage job hierarchy")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="set")
    @require_raid_leader(None)
    async def set(self, interaction, jobs: str, force: bool = False):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            row = set_hierarchy(session, selected(session, interaction), jobs, force)
        await reply(interaction, f"Hierarchy version {row.version} activated.", ephemeral=True)

    @group.command(name="show")
    async def show(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            row = next(
                (h for h in selected(session, interaction).job_hierarchies if h.active), None
            )
            text = (
                "No hierarchy configured."
                if row is None
                else "\n".join(
                    f"{e.position}. {e.job.abbreviation} — {e.job.name}" for e in row.entries
                )
            )
        await reply(interaction, text)

    @group.command(name="history")
    async def history(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            rows = session.scalars(
                select(JobHierarchy)
                .where(JobHierarchy.static_id == selected(session, interaction).id)
                .order_by(JobHierarchy.version.desc())
            ).all()
            lines = [
                f"Version {h.version} — {h.created_at:%Y-%m-%d %H:%M:%S} — "
                f"{'active' if h.active else 'previous'}"
                for h in rows
            ]
        for page in pages(lines):
            await reply(interaction, page)


async def setup(bot):
    await bot.add_cog(Hierarchy(bot))
