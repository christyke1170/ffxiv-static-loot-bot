"""Discord weekly reclear workflow commands backed only by neutral V2 services."""

from typing import Literal

from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import V2Confirmation, V2Plan, V2PlanAssignment, WeeklyLockout
from app.services import close_v2_week, generate_and_persist_weekly_plan
from app.services.reclear import (
    cancel_reclear_week,
    current_week,
    setup_roster,
)
from bot.checks import require_raid_leader
from bot.services.commands import command_session, defer, reply, selected
from bot.views.reclear import ConfirmActionView, SetupPreviewView, roster_text
from bot.views.v2_confirmation import V2ConfirmationView
from bot.views.v2_plan import V2PlanView


def _plan(session, week_id):
    return session.scalar(select(V2Plan).where(V2Plan.reclear_week_id == week_id))


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

    @group.command(name="status", description="Show this reset's neutral V2 status")
    async def status(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            plan = _plan(session, week.id)
            text = (
                f"**Reset week:** {week.week_start}\n"
                f"**Mode:** {week.clear_mode.value.title()}\n"
                f"**Workflow:** {week.workflow_state.value}\n"
                f"**V2 plan:** {'present' if plan else 'not generated'}"
            )
        await reply(interaction, text)

    @group.command(name="plan", description="Generate the current neutral V2 weekly plan")
    @require_raid_leader(None)
    async def plan(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            result = generate_and_persist_weekly_plan(
                session, week.static_id, week.id, interaction.user.id
            )
        await interaction.followup.send(
            view=V2PlanView(self.bot, result, interaction.user.id), ephemeral=True
        )

    @group.command(name="complete", description="Record neutral floor completion")
    @require_raid_leader(None)
    async def complete(self, interaction, floor_number: int):
        await defer(interaction, ephemeral=True)
        if floor_number not in (1, 2, 3, 4):
            raise ValueError("Floor number must be between 1 and 4.")
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            characters = [p.character for g in week.groups for p in g.participants] or [
                c for m in week.static.members if m.active for c in m.characters if c.active
            ]
            for character in characters:
                row = session.scalar(
                    select(WeeklyLockout).where(
                        WeeklyLockout.character_id == character.id,
                        WeeklyLockout.floor_number == floor_number,
                        WeeklyLockout.week_start == week.week_start,
                    )
                )
                if row is None:
                    row = WeeklyLockout(
                        character_id=character.id,
                        floor_number=floor_number,
                        week_start=week.week_start,
                    )
                    session.add(row)
                row.cleared = True
                row.loot_eligible = False
            session.flush()
        await reply(
            interaction,
            f"Floor {floor_number} completion recorded for the neutral V2 week.",
            ephemeral=True,
        )

    @group.command(name="resume", description="Resume at the first pending V2 confirmation")
    async def resume(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            view = _next_confirmation(self.bot, session, week, interaction.user.id)
        if view:
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.followup.send("No pending V2 confirmations remain.", ephemeral=True)

    @group.command(name="close", description="Close a fully resolved V2 reclear")
    @require_raid_leader(None)
    async def close(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            if _next_confirmation(self.bot, session, week, interaction.user.id) is not None:
                await reply(
                    interaction,
                    "Closure blocked: V2 confirmation questions remain.",
                    ephemeral=True,
                )
                return
            close_v2_week(session, week, interaction.user.id)
        await reply(interaction, "V2 reclear closed; audit history was retained.", ephemeral=True)

    @group.command(name="cancel", description="Cancel an untouched neutral reclear week")
    @require_raid_leader(None)
    async def cancel(self, interaction, reason: str):
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            week_id, static_id, start = week.id, week.static_id, week.week_start

        async def cancel_week(callback_interaction):
            with command_session(self.bot) as session:
                static = selected(session, callback_interaction)
                if static.id != static_id:
                    raise ValueError("This cancellation preview is stale.")
                cancel_reclear_week(session, static.id, reason, callback_interaction.user.id)
            await callback_interaction.response.edit_message(
                content="Reclear cancelled; audit history was retained.", view=None
            )

        await interaction.response.send_message(
            view=ConfirmActionView(
                cancel_week, f"Cancel reclear week {start}?\nReason: {reason}", f"rcancel:{week_id}"
            ),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Reclear(bot))
