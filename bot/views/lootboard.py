"""Components V2 database-backed weekly loot board."""

import discord

from app.services.loot_formatting import assignment_detail, loot_board_table
from app.services.reclear import load_loot_board

MAX_COMPONENTS_V2_CHILDREN = 40


class LootBoardView(discord.ui.LayoutView):
    def __init__(self, bot, board, *, page=0, floor_index=0, group=None, detail_id=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.board = board
        self.page = page
        self.floor_index = floor_index
        self.group = group
        self.detail_id = detail_id
        self._build()

    @property
    def floors(self):
        return sorted({(row.floor_number, row.floor_id) for row in self.board.rows})

    def _build(self):
        self.clear_items()
        floor_id = self.floors[self.floor_index][1] if self.floors else None
        table, pages = loot_board_table(
            self.board, floor_id=floor_id, group=self.group, page=self.page
        )
        if self.detail_id:
            row = next(
                (row for row in self.board.rows if row.assignment_id == self.detail_id), None
            )
            body = assignment_detail(row) if row else "Assignment is no longer available."
        else:
            body = table
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## {self.board.static_name} Loot Board\nWeek {self.board.week_start}"
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(body),
                discord.ui.TextDisplay(f"Page {self.page + 1}/{pages}"),
            )
        )
        details = discord.ui.Select(
            custom_id="lb:detail",
            placeholder="Assignment details",
            options=[
                discord.SelectOption(
                    label=f"{r.floor_name} {chr(64 + r.group_number)} — {r.drop_name}"[:100],
                    value=str(r.assignment_id),
                )
                for r in self.board.rows[:25]
            ]
            or [discord.SelectOption(label="No assignments", value="0")],
            disabled=not self.board.rows,
        )
        details.callback = self.details
        self.add_item(discord.ui.ActionRow(details))
        buttons = []
        for label, action in (
            ("Previous floor", "prev"),
            ("Next floor", "next"),
            ("Group A", "a"),
            ("Group B", "b"),
            ("All groups", "all"),
        ):
            button = discord.ui.Button(label=label, custom_id=f"lb:{action}")
            button.callback = getattr(self, action)
            buttons.append(button)
        self.add_item(discord.ui.ActionRow(*buttons))
        refresh = discord.ui.Button(label="Refresh", custom_id="lb:refresh")
        close = discord.ui.Button(
            label="Close view", style=discord.ButtonStyle.danger, custom_id="lb:close"
        )
        refresh.callback = self.refresh
        close.callback = self.close
        self.add_item(discord.ui.ActionRow(refresh, close))
        assert self.total_children_count <= MAX_COMPONENTS_V2_CHILDREN

    async def interaction_check(self, interaction):
        if interaction.guild is None or interaction.guild.id != self.board.guild_id:
            await interaction.response.send_message(
                "This loot board belongs to another guild.", ephemeral=True
            )
            return False
        if interaction.user.id not in self.board.member_discord_user_ids:
            await interaction.response.send_message(
                "Only active static members may use this board.", ephemeral=True
            )
            return False
        return True

    async def prev(self, interaction):
        self.floor_index = max(self.floor_index - 1, 0)
        self.detail_id = None
        self._build()
        await interaction.response.edit_message(view=self)

    async def next(self, interaction):
        self.floor_index = min(self.floor_index + 1, max(len(self.floors) - 1, 0))
        self.detail_id = None
        self._build()
        await interaction.response.edit_message(view=self)

    async def a(self, interaction):
        self.group = 1
        self.detail_id = None
        self._build()
        await interaction.response.edit_message(view=self)

    async def b(self, interaction):
        self.group = 2
        self.detail_id = None
        self._build()
        await interaction.response.edit_message(view=self)

    async def all(self, interaction):
        self.group = None
        self.detail_id = None
        self._build()
        await interaction.response.edit_message(view=self)

    async def details(self, interaction):
        select = next(item for item in self.walk_children() if isinstance(item, discord.ui.Select))
        self.detail_id = int(select.values[0])
        self._build()
        await interaction.response.edit_message(view=self)

    async def refresh(self, interaction):
        with self.bot.session_factory() as session:
            self.board = load_loot_board(session, self.board.static_id)
        self.floor_index = min(self.floor_index, max(len(self.floors) - 1, 0))
        self._build()
        await interaction.response.edit_message(view=self)

    async def close(self, interaction):
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
