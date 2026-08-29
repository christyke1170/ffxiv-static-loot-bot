"""Components V2 weekly roster previews and completion confirmations."""

import discord
from sqlalchemy.exc import SQLAlchemyError

from app.models import ClearMode
from app.services.reclear import create_reclear_week, preview_rosters
from bot.checks import is_raid_leader
from bot.services.commands import command_session, selected


def roster_text(rosters) -> str:
    sections = []
    for index, roster in enumerate(rosters, 1):
        title = "Roster" if len(rosters) == 1 else f"Split {chr(64 + index)}"
        lines = [f"- {character.name} ({character.kind.value.title()})" for character in roster]
        sections.append(f"**{title}**\n" + "\n".join(lines))
    return "\n\n".join(sections)[:1900]


def message_view(text: str) -> discord.ui.LayoutView:
    """Build a component-v2-only view for replacing an interactive message."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(text[:1900])))
    return view


class SetupPreviewView(discord.ui.LayoutView):
    def __init__(self, bot, static_id: int, mode: ClearMode, notes: str | None, members):
        super().__init__(timeout=300)
        self.bot = bot
        self.static_id = static_id
        self.mode = mode
        self.notes = notes
        self.selected_ids: set[int] = set()
        self.members = members
        self._build()

    def _build(self, preview: str | None = None, notice: str | None = None):
        self.clear_items()
        text = preview or (
            "Split groups will be selected automatically by the optimizer."
            if self.mode is ClearMode.SPLIT
            else "Review the Regular roster before creating the week."
        )
        if notice:
            text += f"\n\n{notice}"
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text)))
        confirm = discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.success, custom_id=f"rs:{self.static_id}:ok"
        )
        cancel = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            custom_id=f"rs:{self.static_id}:cancel",
        )
        confirm.callback = self.confirm
        cancel.callback = self.cancel
        self.add_item(discord.ui.ActionRow(confirm, cancel))

    async def interaction_check(self, interaction):
        if not is_raid_leader(interaction, None):
            await interaction.response.send_message(
                "Raid-leader permission is required.", ephemeral=True
            )
            return False
        return True

    async def reselect(self, interaction):
        select = next(item for item in self.walk_children() if isinstance(item, discord.ui.Select))
        self.selected_ids = {int(value) for value in select.values}
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            if static.id != self.static_id:
                raise ValueError("This setup preview is stale for the selected static.")
            rosters = preview_rosters(session, static, self.mode, self.selected_ids)
            preview = roster_text(rosters)
        self._build(preview)
        await interaction.response.edit_message(view=self)

    async def confirm(self, interaction):
        try:
            with command_session(self.bot) as session:
                static = selected(session, interaction)
                if static.id != self.static_id:
                    raise ValueError("This setup preview is stale for the selected static.")
                week = create_reclear_week(
                    session,
                    static,
                    self.mode,
                    # Split membership is deliberately left to the V2 optimizer.
                    split_a_main_member_ids=self.selected_ids or None,
                    notes=self.notes,
                    actor_discord_user_id=interaction.user.id,
                )
                text = f"Reclear week {week.week_start} saved as DRAFT."
            self._build(notice=text)
            self._disable()
            await interaction.response.edit_message(view=self)
            self.stop()
        except (ValueError, SQLAlchemyError) as error:
            self._build(notice=f"Setup could not be saved: {error}")
            self._disable()
            await interaction.response.edit_message(view=self)
            self.stop()

    async def reset_selection(self, interaction):
        self.selected_ids.clear()
        self._build(notice="Selection cleared. Choose exactly four members again.")
        await interaction.response.edit_message(view=self)

    async def cancel(self, interaction):
        self._build(notice="Setup cancelled; nothing was saved.")
        self._disable()
        await interaction.response.edit_message(view=self)
        self.stop()

    def _disable(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True


class ConfirmActionView(discord.ui.LayoutView):
    def __init__(self, on_confirm, text: str, custom_prefix: str):
        super().__init__(timeout=300)
        self.on_confirm = on_confirm
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text[:1900])))
        confirm = discord.ui.Button(
            label="Confirm", style=discord.ButtonStyle.success, custom_id=f"{custom_prefix}:ok"
        )
        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.danger, custom_id=f"{custom_prefix}:cancel"
        )
        confirm.callback = self.confirm
        cancel.callback = self.cancel
        self.add_item(discord.ui.ActionRow(confirm, cancel))

    async def interaction_check(self, interaction):
        if not is_raid_leader(interaction, None):
            await interaction.response.send_message(
                "Raid-leader permission is required.", ephemeral=True
            )
            return False
        return True

    async def confirm(self, interaction):
        await self.on_confirm(interaction)
        self.stop()

    async def cancel(self, interaction):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
