from discord import app_commands
from discord.ext import commands

from app.schemas.needs import NeedStatus
from app.services.board import build_static_gear_board
from app.services.formatting import player_table, safe_text
from bot.services.commands import command_session, defer, reply, selected
from bot.services.gear import character


class Needs(commands.Cog):
    group = app_commands.Group(name="needs", description="Read current database-backed gear needs")

    def __init__(self, bot):
        self.bot = bot

    def board(self, session, interaction):
        return build_static_gear_board(session, selected(session, interaction).id)

    @group.command(name="player")
    async def player(self, interaction, character_name: str):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = character(session, static, character_name)
            board = build_static_gear_board(session, static.id)
            player = next(row for row in board.players if row.character_id == target.id)
            table, warnings = player_table(player)
        await reply(interaction, table + ("\nWarnings: " + "; ".join(warnings) if warnings else ""))

    @group.command(name="floor")
    async def floor(self, interaction, floor_number: int):
        await defer(interaction)
        with command_session(self.bot) as session:
            board = self.board(session, interaction)
            lines = []
            for player in board.players:
                for slot in player.slots:
                    if (
                        slot.required_floor_number == floor_number
                        and slot.required_loot_type
                        and slot.needs_status
                        not in {
                            NeedStatus.COMPLETE,
                            NeedStatus.MANUALLY_COMPLETE,
                            NeedStatus.NOT_APPLICABLE,
                        }
                    ):
                        lines.append(
                            f"{safe_text(player.display_name)} — {safe_text(slot.name)} — "
                            f"{safe_text(slot.required_loot_type)}"
                        )
        await reply(
            interaction,
            f"Floor {floor_number} configured loot needs\n"
            + ("\n".join(lines) or "No matching needs."),
        )

    @group.command(name="augment")
    async def augment(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            board = self.board(session, interaction)
            lines = [
                f"{safe_text(player.display_name)} — "
                + (
                    ", ".join(
                        f"{row.code}: owned {row.owned}, need {row.needed}"
                        for row in player.materials
                    )
                    or "none"
                )
                for player in board.players
            ]
        await reply(interaction, "Augmentation materials\n" + "\n".join(lines))

    @group.command(name="books")
    async def books(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            board = self.board(session, interaction)
            lines = [
                f"{safe_text(player.display_name)} — "
                + (
                    ", ".join(
                        f"F{row.floor_number}: {row.earned}-{row.spent}+"
                        f"{row.manual_adjustment}={row.available}, "
                        f"remaining {row.remaining_required}"
                        for row in player.books
                    )
                    or "none"
                )
                for player in board.players
            ]
        await reply(interaction, "Book balances\n" + "\n".join(lines))


async def setup(bot):
    await bot.add_cog(Needs(bot))
