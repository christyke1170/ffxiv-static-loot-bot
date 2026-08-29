"""Read-only current-week V2 plan board command."""

from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import Character, V2Plan
from app.services import load_persisted_plan_v2
from bot.services.commands import command_session, selected
from bot.views.v2_plan import V2PlanView


class LootBoard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="lootboard", description="Open the current weekly loot board")
    async def lootboard(self, interaction):
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            if not any(
                member.active and member.discord_user_id == interaction.user.id
                for member in static.members
            ):
                raise ValueError("Only active static members can open this loot board.")
            plan = session.scalar(
                select(V2Plan).where(V2Plan.static_id == static.id).order_by(V2Plan.id.desc())
            )
            if plan is None:
                raise ValueError("No loot plan has been generated for this static.")
            result = load_persisted_plan_v2(session, plan.id)
            ids = {
                i for group in getattr(result.proposal, "groups", ()) for i in group.participant_ids
            }
            ids.update(
                assignment.recipient_id
                for assignment in getattr(result.proposal, "assignments", ())
                if assignment.recipient_id is not None
            )
            ids.update(
                row.recipient_id
                for group in result.proposal.groups
                for assignment in group.assignments
                for row in (assignment,)
                if row.recipient_id is not None
            )
            names = {
                row.id: f"{row.name} - {row.kind.value.title()} - {row.job.abbreviation}"
                for row in session.scalars(select(Character).where(Character.id.in_(ids)))
            }
            static_name, reset_date = static.name, result.proposal.week_start
        await interaction.response.send_message(
            view=V2PlanView(self.bot, result, interaction.user.id, names, static_name, reset_date)
        )


async def setup(bot):
    await bot.add_cog(LootBoard(bot))
