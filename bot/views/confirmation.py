"""Restart-safe database-backed reclear confirmation wizard controls."""

import discord
from sqlalchemy import select

from app.models import (
    ConfirmationQuestion,
    LootAssignment,
    LootPlan,
    ReclearWeek,
    ReclearWorkflowState,
)
from app.services import (
    confirm_augmentation_applied,
    confirm_coffer_redemption,
    confirm_loot_received,
    confirmation_queue,
    resolve_character_name,
)
from bot.checks import is_raid_leader
from bot.services.commands import command_session, selected


def confirmation_custom_id(week_id: int, assignment_id: int, action: str) -> str:
    return f"rc:{week_id}:{assignment_id}:{action}"


def question_text(item) -> str:
    row = item.assignment
    recipient = row.final_recipient or row.intended_character
    group = chr(64 + row.reclear_group.group_number)
    if item.question is ConfirmationQuestion.RECEIVED:
        return (
            f"Did {recipient.name} receive the {row.loot_type.name} "
            f"from {row.raid_floor.name} Split {group}?"
        )
    if item.question is ConfirmationQuestion.REDEEMED_CORRECTLY:
        return (
            f"Did {recipient.name} redeem the {row.loot_type.name} "
            f"for the intended BiS {row.intended_bis_set_item.gear_slot.display_name}?"
        )
    return f"Did {recipient.name} apply the {row.loot_type.name} to the intended tome item?"


class NegativeReceiptModal(discord.ui.Modal):
    explanation = discord.ui.TextInput(
        label="Explanation", style=discord.TextStyle.paragraph, required=False, max_length=1000
    )
    actual_recipient = discord.ui.TextInput(
        label="Actual recipient character name", required=False, max_length=100
    )

    def __init__(self, bot, week_id: int, assignment_id: int):
        super().__init__(
            title="Loot was not received",
            custom_id=confirmation_custom_id(week_id, assignment_id, "no-modal"),
        )
        self.bot = bot
        self.week_id = week_id
        self.assignment_id = assignment_id

    async def on_submit(self, interaction):
        with command_session(self.bot) as session:
            static, _ = _validate(session, interaction, self.week_id, self.assignment_id)
            actual = resolve_character_name(
                session, static.id, str(self.actual_recipient.value or "")
            )
            if self.actual_recipient.value and actual is None:
                raise ValueError("Actual recipient character was not found in the selected static.")
            confirm_loot_received(
                session,
                self.assignment_id,
                False,
                interaction.user.id,
                str(self.explanation.value or "") or None,
                actual_recipient_character_id=actual.id if actual else None,
            )
        await _advance(interaction, self.bot, self.week_id, excluded=set())


class ConfirmationView(discord.ui.LayoutView):
    """Persistent controls; state is always loaded from the database on interaction."""

    def __init__(self, bot, week_id: int, assignment_id: int, text: str = "Loot confirmation"):
        super().__init__(timeout=None)
        self.bot = bot
        self.week_id = week_id
        self.assignment_id = assignment_id
        self.skipped: set[int] = set()
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text[:1900])))
        yes = discord.ui.Button(
            label="Yes",
            style=discord.ButtonStyle.success,
            custom_id=confirmation_custom_id(week_id, assignment_id, "yes"),
        )
        no = discord.ui.Button(
            label="No",
            style=discord.ButtonStyle.danger,
            custom_id=confirmation_custom_id(week_id, assignment_id, "no"),
        )
        skip = discord.ui.Button(
            label="Skip for now",
            custom_id=confirmation_custom_id(week_id, assignment_id, "skip"),
        )
        stop = discord.ui.Button(
            label="Stop and resume later",
            custom_id=confirmation_custom_id(week_id, assignment_id, "stop"),
        )
        yes.callback = self.yes
        no.callback = self.no
        skip.callback = self.skip
        stop.callback = self.stop_wizard
        self.add_item(discord.ui.ActionRow(yes, no, skip, stop))

    async def interaction_check(self, interaction):
        try:
            with self.bot.session_factory() as session:
                _validate(session, interaction, self.week_id, self.assignment_id)
            return True
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return False

    async def yes(self, interaction):
        with command_session(self.bot) as session:
            _, item = _validate(session, interaction, self.week_id, self.assignment_id)
            if item.question is ConfirmationQuestion.RECEIVED:
                confirm_loot_received(session, self.assignment_id, True, interaction.user.id)
            elif item.question is ConfirmationQuestion.REDEEMED_CORRECTLY:
                confirm_coffer_redemption(session, self.assignment_id, True, interaction.user.id)
            else:
                confirm_augmentation_applied(session, self.assignment_id, True, interaction.user.id)
        await _advance(interaction, self.bot, self.week_id, self.skipped)

    async def no(self, interaction):
        with self.bot.session_factory() as session:
            _, item = _validate(session, interaction, self.week_id, self.assignment_id)
        if item.question is ConfirmationQuestion.RECEIVED:
            await interaction.response.send_modal(
                NegativeReceiptModal(self.bot, self.week_id, self.assignment_id)
            )
            return
        with command_session(self.bot) as session:
            _, item = _validate(session, interaction, self.week_id, self.assignment_id)
            if item.question is ConfirmationQuestion.REDEEMED_CORRECTLY:
                confirm_coffer_redemption(session, self.assignment_id, False, interaction.user.id)
            else:
                confirm_augmentation_applied(
                    session, self.assignment_id, False, interaction.user.id
                )
        await _advance(interaction, self.bot, self.week_id, self.skipped)

    async def skip(self, interaction):
        self.skipped.add(self.assignment_id)
        await _advance(interaction, self.bot, self.week_id, self.skipped)

    async def stop_wizard(self, interaction):
        self._disable()
        await interaction.response.edit_message(view=self)
        self.stop()

    def _disable(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True


def first_confirmation_view(bot, session, week_id: int):
    queue = confirmation_queue(session, week_id)
    if not queue:
        return None
    item = queue[0]
    return ConfirmationView(bot, week_id, item.assignment.id, question_text(item))


def register_persistent_confirmation_views(bot) -> int:
    with bot.session_factory() as session:
        week_ids = list(
            session.scalars(
                select(ReclearWeek.id).where(
                    ReclearWeek.workflow_state == ReclearWorkflowState.AWAITING_CONFIRMATION
                )
            )
        )
        assignments = [
            item.assignment for week_id in week_ids for item in confirmation_queue(session, week_id)
        ]
        for assignment in assignments:
            bot.add_view(ConfirmationView(bot, assignment.loot_plan.reclear_week_id, assignment.id))
    return len(assignments)


def _validate(session, interaction, week_id: int, assignment_id: int):
    if interaction.guild is None:
        raise ValueError("This confirmation belongs to a Discord guild.")
    if not is_raid_leader(interaction, None):
        raise ValueError("Raid-leader permission is required.")
    static = selected(session, interaction)
    row = session.scalar(
        select(LootAssignment)
        .join(LootPlan)
        .join(ReclearWeek)
        .where(
            LootAssignment.id == assignment_id,
            LootPlan.reclear_week_id == week_id,
            ReclearWeek.static_id == static.id,
        )
    )
    if row is None:
        raise ValueError("This confirmation is stale or belongs to another static.")
    week = session.get(ReclearWeek, week_id)
    if week.workflow_state is not ReclearWorkflowState.AWAITING_CONFIRMATION:
        raise ValueError("This reclear is closed, cancelled, stale, or not awaiting confirmation.")
    item = next(
        (
            item
            for item in confirmation_queue(session, week_id)
            if item.assignment.id == assignment_id
        ),
        None,
    )
    if item is None:
        raise ValueError("This confirmation has already been resolved or is stale.")
    return static, item


async def _advance(interaction, bot, week_id: int, excluded: set[int]):
    with bot.session_factory() as session:
        queue = [
            item
            for item in confirmation_queue(session, week_id)
            if item.assignment.id not in excluded
        ]
        if queue:
            item = queue[0]
            view = ConfirmationView(bot, week_id, item.assignment.id, question_text(item))
            view.skipped = set(excluded)
        else:
            view = None
    if view:
        await interaction.response.edit_message(view=view)
    else:
        await interaction.response.edit_message(
            content="No pending confirmations remain.", view=None
        )
