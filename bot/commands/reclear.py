"""Discord weekly reclear workflow commands backed by neutral planning services."""

from typing import Literal

from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import Character, V2Confirmation, V2Plan, V2PlanAssignment
from app.services import close_v2_week, generate_and_persist_weekly_plan
from app.services.reclear import (
    cancel_reclear_week,
    current_week,
    setup_roster,
)
from bot.checks import require_raid_leader
from bot.services.commands import command_session, defer, reply, selected
from bot.views.reclear import ConfirmActionView, SetupPreviewView, message_view, roster_text
from bot.views.v2_confirmation import V2ConfirmationView
from bot.views.v2_plan import V2PlanView


def _plan(session, week_id):
    return session.scalar(select(V2Plan).where(V2Plan.reclear_week_id == week_id))


def _presentation(session, result):
    proposal = result.proposal
    ids = {
        identifier
        for group in getattr(proposal, "groups", ())
        for identifier in group.participant_ids
    }
    ids.update(
        assignment.recipient_id
        for group in getattr(proposal, "groups", ())
        for assignment in group.assignments
        if assignment.recipient_id is not None
    )
    ids.update(
        assignment.recipient_id
        for assignment in getattr(proposal, "assignments", ())
        if assignment.recipient_id is not None
    )
    characters = (
        session.scalars(select(Character).where(Character.id.in_(ids))).all() if ids else ()
    )
    names = {
        row.id: f"{row.name} - {row.kind.value.title()} - {row.job.abbreviation}"
        for row in characters
    }
    missing = ids - names.keys()
    if missing:
        import logging

        logging.getLogger(__name__).warning(
            "Plan references missing character IDs: %s", sorted(missing)
        )
    return names, proposal.static_name if hasattr(proposal, "static_name") else None


def _next_confirmation(bot, session, week, owner_id):
    plan = _plan(session, week.id)
    if plan is None:
        return None
    for assignment in session.scalars(
        select(V2PlanAssignment)
        .where(V2PlanAssignment.plan_id == plan.id)
        .order_by(V2PlanAssignment.sort_order)
    ):
        keys = (
            ("WEAPON_TOMESTONE", "WEAPON_AUGMENT")
            if "TOME" in assignment.loot_key.upper()
            else (assignment.material_key or assignment.loot_key,)
        )
        confirmations = {
            (row.resource_key, row.action)
            for row in session.scalars(
                select(V2Confirmation).where(V2Confirmation.assignment_id == assignment.id)
            )
        }
        for key in keys:
            if (key, "RECEIPT") not in confirmations:
                return V2ConfirmationView.for_resource(
                    bot, assignment.id, week.static_id, owner_id, key
                )
        if assignment.material_key is None and ("APPLICATION", "APPLICATION") not in confirmations:
            return V2ConfirmationView.for_resource(
                bot, assignment.id, week.static_id, owner_id, keys[0]
            )
    return None


class Reclear(commands.Cog):
    group = app_commands.Group(name="reclear", description="Manage the current weekly reclear")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="setup", description="Preview and create this reset's reclear")
    @require_raid_leader(None)
    async def setup(self, interaction, mode: Literal["Regular", "Split"], notes: str | None = None):
        clear_mode = __import__("app.models", fromlist=["ClearMode"]).ClearMode(mode.upper())
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            members, mains, _ = setup_roster(session, static, clear_mode)
            preview = (
                roster_text((tuple(mains[m.id] for m in members),))
                if clear_mode.value == "REGULAR"
                else None
            )
            static_id = static.id
        view = SetupPreviewView(self.bot, static_id, clear_mode, notes, members)
        if preview:
            view._build(preview)
        await interaction.response.send_message(view=view, ephemeral=True)

    @group.command(name="status", description="Show this reset's loot plan status")
    async def status(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            plan = _plan(session, week.id)
            text = (
                f"**Reset week:** {week.week_start}\n"
                f"**Mode:** {week.clear_mode.value.title()}\n"
                f"**Workflow:** {week.workflow_state.value}\n"
                f"**Loot plan:** {'generated' if plan else 'not generated'}"
            )
        await reply(interaction, text)

    @group.command(name="plan", description="Generate the current weekly loot plan")
    @require_raid_leader(None)
    async def plan(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            result = generate_and_persist_weekly_plan(
                session, week.static_id, week.id, interaction.user.id
            )
            names = _presentation(session, result)[0]
            static_name, reset_date = week.static.name, week.week_start
        await interaction.followup.send(
            view=V2PlanView(self.bot, result, interaction.user.id, names, static_name, reset_date),
            ephemeral=True,
        )

    @group.command(name="complete", description="Announce that this week's reclear is complete")
    @require_raid_leader(None)
    async def complete(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
        await reply(
            interaction,
            f"Reclear complete for the week of {week.week_start}.",
            ephemeral=True,
        )

    @group.command(name="resume", description="Resume at the first pending loot confirmation")
    async def resume(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            view = _next_confirmation(self.bot, session, week, interaction.user.id)
        if view:
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.followup.send("No pending loot confirmations remain.", ephemeral=True)

    @group.command(name="close", description="Close a fully resolved reclear")
    @require_raid_leader(None)
    async def close(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            if _next_confirmation(self.bot, session, week, interaction.user.id) is not None:
                await reply(
                    interaction,
                    "Closure blocked: confirmation questions remain.",
                    ephemeral=True,
                )
                return
            close_v2_week(session, week, interaction.user.id)
        await reply(interaction, "Reclear closed; audit history was retained.", ephemeral=True)

    @group.command(name="cancel", description="Cancel the current weekly loot plan")
    @require_raid_leader(None)
    async def cancel(self, interaction, reason: str):
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            week_id, static_id, start = week.id, week.static_id, week.week_start

        async def cancel_week(callback_interaction):
            try:
                with command_session(self.bot) as session:
                    static = selected(session, callback_interaction)
                    if static.id != static_id:
                        raise ValueError("This cancellation preview is stale.")
                    cancel_reclear_week(session, static.id, reason, callback_interaction.user.id)
                await callback_interaction.response.edit_message(
                    view=message_view("Reclear cancelled; audit history was retained.")
                )
            except ValueError as error:
                await callback_interaction.response.edit_message(
                    view=message_view(f"Reclear could not be cancelled: {error}")
                )

        await interaction.response.send_message(
            view=ConfirmActionView(
                cancel_week, f"Cancel reclear week {start}?\nReason: {reason}", f"rcancel:{week_id}"
            ),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Reclear(bot))
