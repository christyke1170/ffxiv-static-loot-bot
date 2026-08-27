"""Raid-leader weekly loot assignment administration commands."""

from typing import Literal

from discord import app_commands
from discord.ext import commands

from app.models import ConfirmationQuestion, LootAssignmentState
from app.schemas.confirmations import ConfirmationError
from app.services import (
    correct_confirmation,
    mark_assignment_disposition,
    override_assignment,
)
from bot.checks import require_raid_leader
from bot.services.commands import command_session, defer, reply, selected
from bot.services.gear import character


class Loot(commands.Cog):
    group = app_commands.Group(name="loot", description="Manage current weekly loot assignments")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="override", description="Override an assignment's final recipient")
    @require_raid_leader(None)
    async def override(
        self,
        interaction,
        assignment: int,
        new_recipient: str,
        reason: str,
        force: bool = False,
    ):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            recipient = character(session, static, new_recipient)
            row = override_assignment(
                session,
                static.id,
                assignment,
                recipient.id,
                reason,
                interaction.user.id,
                force=force,
            )
        await reply(
            interaction,
            f"Assignment {row.id} final recipient set to {recipient.name}; "
            "original suggestion retained.",
            ephemeral=True,
        )

    @group.command(name="leftover", description="Mark an assignment leftover or free roll")
    @require_raid_leader(None)
    async def leftover(
        self,
        interaction,
        assignment: int,
        disposition: Literal["Leftover", "Free roll"],
        reason: str,
    ):
        await defer(interaction, ephemeral=True)
        state = (
            LootAssignmentState.LEFTOVER
            if disposition == "Leftover"
            else LootAssignmentState.FREE_ROLL
        )
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            row = mark_assignment_disposition(
                session, static.id, assignment, state, reason, interaction.user.id
            )
        await reply(interaction, f"Assignment {row.id} marked {state.value}.", ephemeral=True)

    @group.command(name="correction", description="Safely correct an append-only confirmation")
    @require_raid_leader(None)
    async def correction(
        self,
        interaction,
        assignment: int,
        confirmation: Literal["Received", "Redeemed correctly", "Augment applied"],
        correct_answer: bool,
        reason: str,
    ):
        await defer(interaction, ephemeral=True)
        question = {
            "Received": ConfirmationQuestion.RECEIVED,
            "Redeemed correctly": ConfirmationQuestion.REDEEMED_CORRECTLY,
            "Augment applied": ConfirmationQuestion.AUGMENT_APPLIED,
        }[confirmation]
        try:
            with command_session(self.bot) as session:
                static = selected(session, interaction)
                from app.services import resolve_assignment

                resolve_assignment(session, static.id, assignment)
                correct_confirmation(
                    session,
                    assignment,
                    question,
                    correct_answer,
                    interaction.user.id,
                    reason,
                )
        except ConfirmationError as error:
            message = str(error)
            if "manual intervention required" in message.lower():
                message = "Automatic correction is unsafe. " + message
            await reply(interaction, message, ephemeral=True)
            return
        await reply(interaction, "Confirmation corrected; history was retained.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Loot(bot))
