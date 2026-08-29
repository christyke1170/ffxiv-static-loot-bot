"""Discord commands for neutral V2 confirmation corrections."""

from typing import Literal

from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import V2Confirmation, V2PlanAssignment
from app.services import correct_v2_application, correct_v2_receipt
from bot.checks import require_raid_leader
from bot.services.commands import command_session, defer, reply, selected


class Loot(commands.Cog):
    group = app_commands.Group(name="loot", description="Manage current weekly V2 loot")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="correction", description="Correct an append-only V2 confirmation")
    @require_raid_leader(None)
    async def correction(
        self,
        interaction,
        assignment: int,
        confirmation: Literal["Receipt", "Application"],
        correct_answer: bool,
        reason: str,
    ):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            assignment_row = session.scalar(
                select(V2PlanAssignment).where(V2PlanAssignment.id == assignment)
            )
            if assignment_row is None or assignment_row.plan.static_id != static.id:
                raise ValueError("That V2 assignment does not belong to the selected static.")
            if confirmation == "Receipt":
                resource_key, action = (
                    assignment_row.material_key or assignment_row.loot_key,
                    "RECEIPT",
                )
            else:
                resource_key, action = "APPLICATION", "APPLICATION"
            row = session.scalar(
                select(V2Confirmation).where(
                    V2Confirmation.assignment_id == assignment,
                    V2Confirmation.resource_key == resource_key,
                    V2Confirmation.action == action,
                )
            )
            if row is None:
                raise ValueError("No matching V2 confirmation exists for this assignment.")
            if action == "RECEIPT":
                correct_v2_receipt(session, row.id, correct_answer, interaction.user.id, reason)
            else:
                correct_v2_application(session, row.id, correct_answer, interaction.user.id, reason)
        await reply(interaction, "V2 correction recorded; history was retained.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Loot(bot))
