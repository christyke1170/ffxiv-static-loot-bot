"""Discord weekly reclear workflow commands."""

from typing import Literal

from discord import app_commands
from discord.ext import commands

from app.models import (
    ClearMode,
    LootPlanState,
    ReclearWorkflowState,
)
from app.schemas.planning import LootPlanGenerationError
from app.services import (
    cancel_reclear_week,
    close_reclear_week,
    confirmation_progress,
    current_week,
    generate_weekly_loot_plan,
    load_loot_board,
    mark_reclear_floors_complete,
    reclear_status,
    setup_roster,
)
from bot.checks import require_raid_leader
from bot.services.commands import command_session, defer, reply, selected
from bot.views.confirmation import first_confirmation_view
from bot.views.lootboard import LootBoardView
from bot.views.reclear import ConfirmActionView, SetupPreviewView, roster_text


class Reclear(commands.Cog):
    group = app_commands.Group(name="reclear", description="Manage the current weekly reclear")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="setup", description="Preview and create this reset's reclear")
    @require_raid_leader(None)
    async def setup(
        self,
        interaction,
        mode: Literal["Regular", "Split"],
        notes: str | None = None,
    ):
        clear_mode = ClearMode(mode.upper())
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            members, mains, _ = setup_roster(session, static, clear_mode)
            if clear_mode is ClearMode.REGULAR:
                preview = roster_text((tuple(mains[member.id] for member in members),))
            else:
                preview = None
            static_id = static.id
        view = SetupPreviewView(self.bot, static_id, clear_mode, notes, members)
        if preview:
            view._build(preview)
        await interaction.response.send_message(view=view, ephemeral=True)

    @group.command(name="status", description="Show this reset's database-backed reclear status")
    async def status(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            status = reclear_status(session, selected(session, interaction).id)
            groups = "\n".join(
                f"Split {chr(64 + group.group_number)}: "
                + ", ".join(
                    f"{entry.character_name} ({entry.kind.title()})" for entry in group.entries
                )
                for group in status.groups
            )
            completed = (
                ", ".join(f"{floor} Split {chr(64 + group)}" for group, floor in status.completions)
                or "None"
            )
            text = (
                f"**Reset week:** {status.week_start}\n**Mode:** {status.mode.value.title()}\n"
                f"**Workflow:** {status.workflow_state.value}\n**Tier:** {status.tier_name}\n"
                f"**Hierarchy snapshot:** {', '.join(status.hierarchy) or 'None'}\n"
                f"**Rosters**\n{groups}\n**Completed:** {completed}\n"
                f"**Loot plan:** {status.plan_state}\n"
                f"**Confirmations:** {status.confirmation_summary}\n"
                f"**Distribution errors:** {status.distribution_errors}\n"
                f"**Can close:** {'Yes' if status.can_close else 'No'}"
            )
        await reply(interaction, text)

    @group.command(name="plan", description="Validate and generate this reset's loot plan")
    @require_raid_leader(None)
    async def plan(self, interaction):
        await defer(interaction, ephemeral=True)
        try:
            with command_session(self.bot) as session:
                week = current_week(session, selected(session, interaction).id)
                result = generate_weekly_loot_plan(session, week.id)
                result.plan.state = LootPlanState.ACTIVE
                if week.workflow_state is ReclearWorkflowState.DRAFT:
                    week.workflow_state = ReclearWorkflowState.PLANNED
                board = load_loot_board(session, week.static_id)
                reused = result.reused_existing_plan
        except LootPlanGenerationError as error:
            issues = error.validation.issues if error.validation else []
            text = "Planning blocked:\n" + "\n".join(f"- {issue.message}" for issue in issues)
            await reply(interaction, text, ephemeral=True)
            return
        view = LootBoardView(self.bot, board)
        await interaction.followup.send(
            f"{'Existing' if reused else 'Generated'} plan with {len(board.rows)} assignments.",
            view=view,
            ephemeral=True,
        )

    @group.command(name="complete", description="Preview floor/group completion records")
    @require_raid_leader(None)
    async def complete(
        self,
        interaction,
        floor: str | None = None,
        group: Literal["A", "B"] | None = None,
    ):
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            floors = sorted(week.raid_tier.floors, key=lambda row: row.floor_number)
            if floor:
                floors = [row for row in floors if row.name.lower() == floor.lower()]
                if not floors:
                    raise ValueError("Unknown floor name for the active tier.")
            groups = sorted(week.groups, key=lambda row: row.group_number)
            if group:
                number = ord(group) - 64
                groups = [row for row in groups if row.group_number == number]
                if not groups:
                    raise ValueError("That group is not configured for this reclear.")
            pairs = [(group_row.id, floor_row.id) for floor_row in floors for group_row in groups]
            labels = [
                f"- {floor_row.name} — Split {chr(64 + group_row.group_number)}"
                for floor_row in floors
                for group_row in groups
            ]
            week_id = week.id

        async def record(callback_interaction):
            with command_session(self.bot) as session:
                selected_static = selected(session, callback_interaction)
                active = current_week(session, selected_static.id)
                if active.id != week_id:
                    raise ValueError("This completion preview is stale.")
                mark_reclear_floors_complete(session, week_id, pairs, callback_interaction.user.id)
                view = first_confirmation_view(self.bot, session, week_id)
            if view:
                await callback_interaction.response.edit_message(view=view)
            else:
                await callback_interaction.response.edit_message(
                    content="Floor completion recorded; no loot questions are pending.", view=None
                )

        view = ConfirmActionView(
            record,
            "**The following completions will be recorded:**\n" + "\n".join(labels),
            f"rcomp:{week_id}",
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    @group.command(name="resume", description="Resume at the first pending loot question")
    @require_raid_leader(None)
    async def resume(self, interaction):
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            view = first_confirmation_view(self.bot, session, week.id)
        if view:
            await interaction.response.send_message(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(
                "No pending confirmations remain.", ephemeral=True
            )

    @group.command(name="close", description="Close a fully resolved reclear")
    @require_raid_leader(None)
    async def close(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            progress = confirmation_progress(session, week.id)
            if week.workflow_state not in {
                ReclearWorkflowState.AWAITING_CONFIRMATION,
                ReclearWorkflowState.CONFIRMED,
                ReclearWorkflowState.CLOSED,
            }:
                await reply(
                    interaction,
                    "Closure blocked: the reclear has not reached confirmation.",
                    ephemeral=True,
                )
                return
            pending = (
                progress.pending_receipt_questions
                + progress.pending_redemption_questions
                + progress.pending_augmentation_questions
            )
            if pending:
                await reply(
                    interaction,
                    f"Closure blocked: {pending} confirmation question(s) remain "
                    f"({progress.pending_receipt_questions} receipt, "
                    f"{progress.pending_redemption_questions} redemption, "
                    f"{progress.pending_augmentation_questions} augmentation).",
                    ephemeral=True,
                )
                return
            close_reclear_week(session, week.id)
            summary = (
                f"Reclear closed: {progress.fully_resolved_assignments}/"
                f"{progress.total_planned_assignments} assignments resolved; "
                f"{progress.failed_assignments} failed; {progress.leftovers} leftover/free roll."
            )
        await reply(interaction, summary, ephemeral=True)

    @group.command(name="cancel", description="Cancel an untouched reclear week")
    @require_raid_leader(None)
    async def cancel(self, interaction, reason: str):
        with command_session(self.bot) as session:
            week = current_week(session, selected(session, interaction).id)
            week_id = week.id
            static_id = week.static_id

        async def cancel_week(callback_interaction):
            with command_session(self.bot) as session:
                static = selected(session, callback_interaction)
                if static.id != static_id:
                    raise ValueError("This cancellation preview is stale.")
                cancel_reclear_week(session, static.id, reason, callback_interaction.user.id)
            await callback_interaction.response.edit_message(
                content="Reclear cancelled; audit history was retained.", view=None
            )

        view = ConfirmActionView(
            cancel_week,
            f"Cancel reclear week {week.week_start}?\nReason: {reason}",
            f"rcancel:{week_id}",
        )
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Reclear(bot))
