"""Private, scoped setup views shared by roster and BiS setup."""

import discord

from bot.checks import is_raid_leader
from bot.services.commands import command_session, selected


class SetupView(discord.ui.LayoutView):
    def __init__(self, bot, static_id, owner_id, guild_id, *, leader_only=True):
        super().__init__(timeout=600)
        self.bot = bot
        self.static_id = static_id
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.leader_only = leader_only

    async def interaction_check(self, interaction):
        if (
            interaction.guild is None
            or interaction.guild.id != self.guild_id
            or interaction.user.id != self.owner_id
            or (self.leader_only and not is_raid_leader(interaction, None))
            or self.is_finished()
        ):
            await interaction.response.send_message(
                "You cannot use this setup view.", ephemeral=True
            )
            return False
        try:
            with command_session(self.bot) as session:
                self.target_static(session, interaction)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return False
        return True

    def target_static(self, session, interaction):
        static = selected(session, interaction)
        if static.id != self.static_id or not static.active:
            raise ValueError("This setup view is stale. Reopen it for the selected static.")
        return static

    def text(self, value):
        self.add_item(discord.ui.TextDisplay(value))

    def button(self, label, callback, *, disabled=False):
        button = discord.ui.Button(label=label, disabled=disabled)
        button.callback = callback
        return button

    async def on_timeout(self):
        for child in self.walk_children():
            if hasattr(child, "disabled"):
                child.disabled = True


def remember_selection(control):
    """Retain client selections across later explicit renders without editing now."""
    for option in control.options:
        option.default = option.value in control.values
