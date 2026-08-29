"""Discord Components V2 interactive static gear board."""

import discord

from app.schemas.board import StaticGearBoard
from app.services.board import build_static_gear_board
from app.services.formatting import (
    LEGEND,
    overview_table,
    player_books,
    player_table,
    summary_table,
)

GEARBOARD_TIMEOUT = 300.0
MAX_COMPONENTS_V2_CHILDREN = 40
STATIC_OVERVIEW_VALUE = "__static_overview__"


class GearBoardView(discord.ui.LayoutView):
    def __init__(self, bot, board: StaticGearBoard, *, page: int = 0, mode: str = "overview"):
        super().__init__(timeout=GEARBOARD_TIMEOUT)
        self.bot = bot
        self.board = board
        self.page = page
        self.mode = mode
        self.selected_player_id = (
            int(mode.partition(":")[2]) if mode.startswith("player:") else None
        )
        self.closed = False
        self._build()

    @property
    def page_count(self) -> int:
        return max((len(self.board.players) + 3) // 4, 1)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.board.guild_id:
            await interaction.response.send_message(
                "This gear board belongs to another guild.", ephemeral=True
            )
            return False
        if interaction.user.id not in self.board.member_discord_user_ids:
            await interaction.response.send_message(
                "Only active static members can use this gear board.", ephemeral=True
            )
            return False
        return True

    def _build(self, notice: str | None = None) -> None:
        self.clear_items()
        title = f"## {self.board.static_name}"
        if self.mode == "summary":
            table, warnings = summary_table(self.board)
            subtitle = "Summary"
        elif self.mode.startswith("player:"):
            player_id = int(self.mode.partition(":")[2])
            player = next(
                (row for row in self.board.players if row.character_id == player_id), None
            )
            if player is None:
                table, warnings, subtitle = (
                    "Player is no longer available.",
                    (),
                    "Stale player selection",
                )
            else:
                table, warnings = player_table(player)
                table = f"{table}\n{player_books(player)}"
                subtitle = f"{player.display_name} - {player.character_name} ({player.job or '?'})"
        else:
            table, warnings = overview_table(self.board, self.page)
            subtitle = f"Overview - page {self.page + 1}/{self.page_count}"
        warning_text = "\n".join(f"- {warning}" for warning in warnings)
        footer = (
            f"**Completion:** {sum(player.complete_slots for player in self.board.players)}/"
            f"{sum(player.applicable_slots for player in self.board.players)} slots\n"
            f"**Last refresh:** <t:{int(self.board.refreshed_at.timestamp())}:F>"
        )
        if warning_text:
            footer += f"\n**Warnings**\n{warning_text}"
        if notice:
            footer += f"\n{notice}"
        footer_text = footer if self.mode == "summary" else f"{LEGEND}\n{footer}"
        container = discord.ui.Container(
            discord.ui.TextDisplay(title),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"**{subtitle}**\n{table}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(footer_text),
        )
        self.add_item(container)
        options = [
            discord.SelectOption(
                label="Static Overview",
                value=STATIC_OVERVIEW_VALUE,
                description="Return to the full gearboard",
            ),
            *(
                discord.SelectOption(
                    label=f"{player.display_name} ({player.job or '?'})"[:100],
                    value=str(player.character_id),
                    description=player.character_name[:100],
                )
                for player in self.board.players
            ),
        ]
        if options:
            select = discord.ui.Select(
                placeholder="Select player", options=options, custom_id="gearboard:player"
            )
            select.callback = self.select_player
            self.add_item(discord.ui.ActionRow(select))
        previous = discord.ui.Button(
            label="Previous", custom_id="gearboard:previous", disabled=self.page <= 0
        )
        next_button = discord.ui.Button(
            label="Next", custom_id="gearboard:next", disabled=self.page >= self.page_count - 1
        )
        refresh = discord.ui.Button(label="Refresh", custom_id="gearboard:refresh")
        summary = discord.ui.Button(label="Summary", custom_id="gearboard:summary")
        close = discord.ui.Button(
            label="Close", style=discord.ButtonStyle.danger, custom_id="gearboard:close"
        )
        previous.callback = self.previous_page
        next_button.callback = self.next_page
        refresh.callback = self.refresh
        summary.callback = self.show_summary
        close.callback = self.close
        self.add_item(discord.ui.ActionRow(previous, next_button, refresh, summary, close))
        assert self.total_children_count <= MAX_COMPONENTS_V2_CHILDREN

    async def previous_page(self, interaction):
        self.page = max(self.page - 1, 0)
        self.mode = "overview"
        self.selected_player_id = None
        self._build()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction):
        self.page = min(self.page + 1, self.page_count - 1)
        self.mode = "overview"
        self.selected_player_id = None
        self._build()
        await interaction.response.edit_message(view=self)

    async def refresh(self, interaction):
        try:
            with self.bot.session_factory() as session:
                self.board = build_static_gear_board(session, self.board.static_id)
            self.page = min(self.page, self.page_count - 1)
            self._build()
        except (LookupError, ValueError):
            self._disable_all()
            self._build("Warning: This static selection is stale or unavailable.")
            self._disable_all()
            self.stop()
        await interaction.response.edit_message(view=self)

    async def select_player(self, interaction):
        select = next(
            (item for item in self.walk_children() if isinstance(item, discord.ui.Select)), None
        )
        values = getattr(select, "values", ())
        if values:
            if values[0] == STATIC_OVERVIEW_VALUE:
                self.mode = "overview"
                self.selected_player_id = None
            else:
                self.selected_player_id = int(values[0])
                self.mode = f"player:{self.selected_player_id}"
        self._build()
        await interaction.response.edit_message(view=self)

    async def show_summary(self, interaction):
        self.mode = "summary"
        self._build()
        await interaction.response.edit_message(view=self)

    async def close(self, interaction):
        self.closed = True
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        self._disable_all()
        if getattr(self, "message", None) is not None:
            await self.message.edit(view=self)

    def _disable_all(self):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
