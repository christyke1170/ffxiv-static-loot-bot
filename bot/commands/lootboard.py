"""Read-only current-week loot board command."""

from discord import app_commands
from discord.ext import commands

from app.services import load_loot_board
from bot.services.commands import command_session, selected
from bot.views.lootboard import LootBoardView


class LootBoard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="lootboard", description="Open the current weekly loot board")
    async def lootboard(self, interaction):
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            if not any(
                member.active and member.discord_user_id == interaction.user.id
                for member in static.members
            ):
                raise ValueError("Only active static members can open this loot board.")
            board = load_loot_board(session, static.id)
        await interaction.response.send_message(view=LootBoardView(self.bot, board))


async def setup(bot):
    await bot.add_cog(LootBoard(bot))
