"""Unregistered Discord controls for neutral V2 confirmation and correction."""

from __future__ import annotations

import discord

from app.models import V2PlanAssignment
from app.services import (
    V2ConfirmationError,
    confirm_v2_application,
    confirm_v2_receipt,
    correct_v2_application,
    correct_v2_receipt,
    read_v2_confirmation_state,
    read_v2_correction_history,
    reverse_v2_application,
)
from bot.checks import is_bot_admin
from bot.services.commands import command_session, selected


def confirmation_state_text(state) -> str:
    """Format immutable confirmation state without exposing internal IDs."""
    lines = ["**V2 confirmation state**"]
    for row in state.confirmations:
        outcome = "confirmed" if row.success else "rejected"
        lines.append(f"- {row.resource_key}: {row.action.lower()} {outcome} (x{row.quantity})")
    if state.effects:
        lines.append(
            "Applied effects: "
            + ", ".join(f"{row.slot_key} -> {row.after_category}" for row in state.effects)
        )
    if state.balances:
        lines.append(
            "Owned resources: "
            + ", ".join(f"{key} x{quantity}" for key, quantity in state.balances if quantity)
        )
    return "\n".join(lines)[:1990]


class V2CorrectionModal(discord.ui.Modal, title="V2 administrator correction"):
    reason = discord.ui.TextInput(label="Reason", required=True, min_length=1, max_length=1000)

    def __init__(self, view, action: str, confirmation_id: int):
        super().__init__()
        self.view = view
        self.action = action
        self.confirmation_id = confirmation_id

    async def on_submit(self, interaction):
        try:
            with command_session(self.view.bot) as session:
                if self.action == "reverse":
                    reverse_v2_application(
                        session, self.confirmation_id, interaction.user.id, str(self.reason)
                    )
                elif self.action == "receipt-failed":
                    correct_v2_receipt(
                        session, self.confirmation_id, False, interaction.user.id, str(self.reason)
                    )
                elif self.action == "receipt-success":
                    correct_v2_receipt(
                        session, self.confirmation_id, True, interaction.user.id, str(self.reason)
                    )
                elif self.action == "application-failed":
                    correct_v2_application(
                        session, self.confirmation_id, False, interaction.user.id, str(self.reason)
                    )
                else:
                    correct_v2_application(
                        session, self.confirmation_id, True, interaction.user.id, str(self.reason)
                    )
            await interaction.response.send_message(
                "V2 correction recorded; history was retained.", ephemeral=True
            )
        except (V2ConfirmationError, ValueError) as error:
            await interaction.response.send_message(str(error)[:500], ephemeral=True)


class V2ConfirmationView(discord.ui.LayoutView):
    """Admin-only receipt/application controller, intentionally not registered."""

    def __init__(self, bot, assignment_id: int, static_id: int, owner_id: int, resource_key: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.assignment_id = assignment_id
        self.static_id = static_id
        self.owner_id = owner_id
        self.resource_key = resource_key
        self.recipient_id = None
        self._build()

    def _build(self):
        self.clear_items()
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "**V2 confirmation workflow**\n"
                    "Use the controls below; state is resumed from the database."
                )
            )
        )
        with self.bot.session_factory() as session:
            assignment = session.get(V2PlanAssignment, self.assignment_id)
            participants = assignment.run.participants if assignment is not None else ()
            if assignment is not None and assignment.recipient_id is None:
                recipient = discord.ui.Select(
                    placeholder="Choose the explicit recipient",
                    custom_id=f"v2:{self.assignment_id}:recipient",
                    options=[
                        discord.SelectOption(
                            label=participant.character.name[:100],
                            value=str(participant.character_id),
                        )
                        for participant in participants
                    ],
                )
                recipient.callback = self.select_recipient
                self.add_item(discord.ui.ActionRow(recipient))
        received = discord.ui.Button(label="Receipt confirmed", style=discord.ButtonStyle.success)
        rejected = discord.ui.Button(label="Receipt rejected", style=discord.ButtonStyle.danger)
        application = discord.ui.Button(label="Apply / redeem", style=discord.ButtonStyle.success)
        refresh = discord.ui.Button(label="Refresh state")
        received.callback = self.receipt_success
        rejected.callback = self.receipt_failure
        application.callback = self.application
        refresh.callback = self.refresh
        self.add_item(discord.ui.ActionRow(received, rejected, application, refresh))
        reverse = discord.ui.Button(label="Reverse application", style=discord.ButtonStyle.danger)
        reverse.callback = self.reverse
        self.add_item(discord.ui.ActionRow(reverse))

    @classmethod
    def for_resource(
        cls, bot, assignment_id: int, static_id: int, owner_id: int, resource_key: str
    ):
        """Build a receipt controller for one persisted logical resource."""
        return cls(bot, assignment_id, static_id, owner_id, resource_key)

    async def interaction_check(self, interaction):
        if not is_bot_admin(interaction, None):
            await interaction.response.send_message(
                "Administrator permission is required.", ephemeral=True
            )
            return False
        if interaction.guild is None:
            await interaction.response.send_message(
                "This workflow requires a Discord guild.", ephemeral=True
            )
            return False
        try:
            with self.bot.session_factory() as session:
                static = selected(session, interaction)
                if (
                    static.id != self.static_id
                    or static.guild.discord_guild_id != interaction.guild.id
                ):
                    await interaction.response.send_message(
                        "This workflow is for another selected static.", ephemeral=True
                    )
                    return False
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return False
        return True

    async def _run(self, interaction, operation):
        try:
            with command_session(self.bot) as session:
                operation(session, interaction.user.id)
                read_v2_confirmation_state(session, self.assignment_id)
            self._build()
            await interaction.response.edit_message(view=self)
        except (V2ConfirmationError, ValueError) as error:
            await interaction.response.send_message(str(error)[:500], ephemeral=True)

    async def receipt_success(self, interaction):
        await self._run(
            interaction,
            lambda session, actor: confirm_v2_receipt(
                session,
                self.assignment_id,
                self.resource_key,
                True,
                actor_id=actor,
                recipient_id=self.recipient_id,
            ),
        )

    async def receipt_failure(self, interaction):
        await self._run(
            interaction,
            lambda session, actor: confirm_v2_receipt(
                session,
                self.assignment_id,
                self.resource_key,
                False,
                actor_id=actor,
                recipient_id=self.recipient_id,
            ),
        )

    async def application(self, interaction):
        await self._run(
            interaction,
            lambda session, actor: confirm_v2_application(
                session,
                self.assignment_id,
                True,
                actor_id=actor,
                recipient_id=self.recipient_id,
            ),
        )

    async def select_recipient(self, interaction):
        select = next(
            child
            for child in self.walk_children()
            if getattr(child, "custom_id", None) == f"v2:{self.assignment_id}:recipient"
        )
        self.recipient_id = int(select.values[0])
        selected_id = self.recipient_id
        self._build()
        self.recipient_id = selected_id
        await interaction.response.edit_message(view=self)

    async def reverse(self, interaction):
        with self.bot.session_factory() as session:
            state = read_v2_confirmation_state(session, self.assignment_id)
            application = next(
                (row for row in state.confirmations if row.action == "APPLICATION"), None
            )
        if application is None:
            await interaction.response.send_message(
                "No application has been confirmed for this assignment.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            V2CorrectionModal(self, "reverse", application.confirmation_id)
        )

    async def refresh(self, interaction):
        try:
            with command_session(self.bot) as session:
                read_v2_confirmation_state(session, self.assignment_id)
            self._build()
            await interaction.response.edit_message(view=self)
        except (V2ConfirmationError, ValueError) as error:
            await interaction.response.send_message(str(error)[:500], ephemeral=True)

    async def on_timeout(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True


def correction_history_text(session, assignment_id: int) -> str:
    history = read_v2_correction_history(session, assignment_id)
    if not history:
        return "No V2 correction history has been recorded."
    return (
        "**V2 correction history**\n"
        + "\n".join(
            f"- {row.correction_type.replace('_', ' ').title()}: {row.reason} (actor recorded)"
            for row in history
        )[:1990]
    )
