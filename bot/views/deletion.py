"""Administrator confirmation views for destructive operations."""

import discord

from bot.checks import is_bot_admin


class DeleteConfirmationView(discord.ui.View):
    def __init__(self, confirm_callback, prompt: str):
        super().__init__(timeout=300)
        self.confirm_callback = confirm_callback
        self.prompt = prompt
        confirm = discord.ui.Button(label="Confirm delete", style=discord.ButtonStyle.danger)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        confirm.callback = self.confirm
        cancel.callback = self.cancel
        self.add_item(confirm)
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_bot_admin(interaction, None):
            await interaction.response.send_message(
                "Administrator permission is required.", ephemeral=True
            )
            return False
        return True

    async def confirm(self, interaction: discord.Interaction) -> None:
        try:
            message = await self.confirm_callback(interaction)
        except ValueError as error:
            await interaction.response.edit_message(content=str(error), view=None)
        else:
            await interaction.response.edit_message(content=message, view=None)
        self.stop()

    async def cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Deletion cancelled; no changes were written.", view=None
        )
