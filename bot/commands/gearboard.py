from discord import app_commands
from discord.ext import commands

from app.services.board import build_static_gear_board
from bot.services.commands import command_session, selected
from bot.views.gearboard import GearBoardView


class GearBoard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="gearboard", description="Open the static's current gear board")
    async def gearboard(self, interaction):
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            if not any(
                member.active and member.discord_user_id == interaction.user.id
                for member in static.members
            ):
                raise ValueError("Only active static members can open this gear board.")
            board = build_static_gear_board(session, static.id)
        view = GearBoardView(self.bot, board)
        await interaction.response.send_message(view=view)


async def setup(bot):
    await bot.add_cog(GearBoard(bot))
