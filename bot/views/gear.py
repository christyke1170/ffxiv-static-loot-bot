"""Ephemeral classification-only current gear editor."""

import discord
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import (
    Character,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    GearClassification,
    GearSlot,
    GearSlotCode,
    RaidFloor,
    Static,
)
from app.services.formatting import SLOT_LABEL
from app.services.gear import clear_gear, set_available_books, set_gear
from bot.checks import is_raid_leader
from bot.services.commands import command_session, selected

GEAR_EDITOR_TIMEOUT = 600.0
MAX_COMPONENTS = 25
MAX_MODAL_INPUTS = 5
MAX_BOOK_BALANCE = 1_000_000
STATE_LABELS = {
    GearClassification.CRAFTED_EX: "Crafted / EX",
    GearClassification.SAVAGE: "Savage",
    GearClassification.TOME: "Tome",
    GearClassification.AUGMENTED_TOME: "Augmented Tome",
    GearClassification.GARBAGE: "Garbage",
}


class BookAdjustmentModal(discord.ui.Modal):
    def __init__(self, editor: "GearEditorView", books: tuple[tuple[int, int, int], ...]):
        super().__init__(title="Adjust Books", custom_id="gear-editor:books-modal")
        self.editor = editor
        self.fields: list[tuple[int, discord.ui.TextInput]] = []
        for index, (floor_id, floor_number, available) in enumerate(books):
            field = discord.ui.TextInput(
                label=f"Floor {floor_number} Books",
                custom_id=f"gear-editor:book:{index}",
                default=str(available),
                required=True,
                min_length=1,
                max_length=len(str(MAX_BOOK_BALANCE)),
            )
            self.fields.append((floor_id, field))
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.editor._authorized(interaction):
            return
        desired: dict[int, int] = {}
        for floor_id, field in self.fields:
            raw = str(field.value or "")
            if not raw.isascii() or not raw.isdecimal():
                await interaction.response.send_message(
                    "Every book balance must be a nonnegative whole number.", ephemeral=True
                )
                return
            value = int(raw)
            if value > MAX_BOOK_BALANCE:
                await interaction.response.send_message(
                    f"Book balances cannot exceed {MAX_BOOK_BALANCE:,}.", ephemeral=True
                )
                return
            desired[floor_id] = value
        try:
            with command_session(self.editor.bot) as session:
                static, character = self.editor._validated_target(session, interaction)
                set_available_books(
                    session,
                    static,
                    character,
                    desired,
                    interaction.user.id,
                    maximum=MAX_BOOK_BALANCE,
                )
        except ValueError:
            await interaction.response.send_message(
                "You cannot use this gear editor.", ephemeral=True
            )
            return
        self.editor._build()
        await interaction.response.edit_message(content=self.editor.content, view=self.editor)


class GearEditorView(discord.ui.View):
    def __init__(
        self, bot, static_id: int, member_id: int, character_id: int, owner_id: int, guild_id: int
    ):
        super().__init__(timeout=GEAR_EDITOR_TIMEOUT)
        self.bot = bot
        self.static_id = static_id
        self.member_id = member_id
        self.character_id = character_id
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.selected_slot: GearSlotCode | None = None
        self.closed = False
        self.content = ""
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self._authorized(interaction)

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild is None
            or interaction.guild.id != self.guild_id
            or interaction.user.id != self.owner_id
            or not is_raid_leader(interaction, None)
        ):
            await interaction.response.send_message(
                "You cannot use this gear editor.", ephemeral=True
            )
            return False
        try:
            with command_session(self.bot) as session:
                self._validated_target(session, interaction)
        except ValueError:
            await interaction.response.send_message(
                "You cannot use this gear editor.", ephemeral=True
            )
            return False
        return True

    def _validated_target(self, session, interaction) -> tuple[Static, Character]:
        static = selected(session, interaction)
        if static.id != self.static_id:
            raise ValueError("This gear editor is stale.")
        character = session.scalar(
            select(Character).where(
                Character.id == self.character_id,
                Character.active.is_(True),
                Character.static_member_id == self.member_id,
                Character.static_member.has(active=True),
                Character.static_member.has(static_id=self.static_id),
            )
        )
        if character is None:
            raise ValueError("This gear editor is stale.")
        return static, character

    def _load(self):
        with command_session(self.bot) as session:
            character = session.scalar(
                select(Character)
                .where(
                    Character.id == self.character_id,
                    Character.active.is_(True),
                    Character.static_member_id == self.member_id,
                    Character.static_member.has(active=True),
                    Character.static_member.has(static_id=self.static_id),
                )
                .options(joinedload(Character.job))
            )
            if character is None:
                raise ValueError("This character is no longer available.")
            slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
            current = {
                row.gear_slot_id: row.current_classification
                for row in session.scalars(
                    select(CharacterGearSlot).where(
                        CharacterGearSlot.character_id == self.character_id
                    )
                )
            }
            static = session.get(Static, self.static_id)
            if static is None:
                raise ValueError("This gear editor is stale.")
            floors = (
                list(
                    session.scalars(
                        select(RaidFloor)
                        .where(RaidFloor.raid_tier_id == static.active_raid_tier_id)
                        .order_by(RaidFloor.floor_number)
                    )
                )
                if static.active_raid_tier_id is not None
                else []
            )
            balances = {
                row.raid_floor_id: row.available
                for row in session.scalars(
                    select(CharacterFloorBookBalance).where(
                        CharacterFloorBookBalance.character_id == self.character_id,
                        CharacterFloorBookBalance.raid_floor_id.in_([floor.id for floor in floors]),
                    )
                )
            }
            return (
                character.static_member.display_name,
                character.kind.value.title(),
                character.job.abbreviation,
                character.job.uses_offhand,
                slots,
                current,
                tuple(
                    (floor.id, floor.floor_number, balances.get(floor.id, 0)) for floor in floors
                ),
            )

    def _build(self, notice: str | None = None) -> None:
        self.clear_items()
        name, kind, job, uses_offhand, slots, current, books = self._load()
        offhand_not_applicable = not uses_offhand
        options = []
        for slot in slots:
            state = current.get(slot.id)
            state_label = (
                "N/A"
                if slot.code is GearSlotCode.OFFHAND and offhand_not_applicable
                else STATE_LABELS.get(state, "Missing")
            )
            options.append(
                discord.SelectOption(
                    label=SLOT_LABEL[slot.code.value],
                    value=slot.code.value,
                    description=f"Current: {state_label}",
                    default=slot.code is self.selected_slot,
                )
            )
        slot_select = discord.ui.Select(
            placeholder="Choose a gear slot", options=options, custom_id="gear-editor:slot"
        )
        slot_select.callback = self.select_slot
        self.add_item(slot_select)

        selected_is_na = self.selected_slot is GearSlotCode.OFFHAND and offhand_not_applicable
        state_select = discord.ui.Select(
            placeholder="Choose current state",
            options=[
                discord.SelectOption(label=label, value=value.value)
                for value, label in STATE_LABELS.items()
            ],
            custom_id="gear-editor:state",
            disabled=self.selected_slot is None or selected_is_na,
        )
        state_select.callback = self.select_state
        self.add_item(state_select)

        reset = discord.ui.Button(
            label="Clear / Reset slot",
            custom_id="gear-editor:reset",
            disabled=self.selected_slot is None or selected_is_na,
        )
        close = discord.ui.Button(
            label="Close", style=discord.ButtonStyle.danger, custom_id="gear-editor:close"
        )
        adjust_books = discord.ui.Button(label="Adjust Books", custom_id="gear-editor:adjust-books")
        reset.callback = self.reset_slot
        adjust_books.callback = self.adjust_books
        close.callback = self.close
        self.add_item(reset)
        self.add_item(adjust_books)
        self.add_item(close)
        selected_label = SLOT_LABEL[self.selected_slot.value] if self.selected_slot else "None"
        self.content = (
            f"**{discord.utils.escape_markdown(name)} · {kind} · {job}**\n"
            f"Selected slot: **{selected_label}**\n\n"
            "**Books**\n"
            + (
                "\n".join(f"Floor {number}: {available}" for _, number, available in books)
                or "None"
            )
        )
        if selected_is_na:
            self.content += " — **N/A for this job**"
        if notice:
            self.content += f"\n{notice}"
        assert len(list(self.walk_children())) <= MAX_COMPONENTS

    async def adjust_books(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        *_, books = self._load()
        if len(books) > MAX_MODAL_INPUTS:
            await interaction.response.send_message(
                "This tier has too many floors to adjust in one modal.", ephemeral=True
            )
            return
        if not books:
            await interaction.response.send_message(
                "This tier has no configured floors.", ephemeral=True
            )
            return
        await interaction.response.send_modal(BookAdjustmentModal(self, books))

    def _selected_value(self, custom_id: str) -> str | None:
        component = next(
            (
                item
                for item in self.walk_children()
                if isinstance(item, discord.ui.Select) and item.custom_id == custom_id
            ),
            None,
        )
        values = getattr(component, "values", ())
        return values[0] if values else None

    async def select_slot(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        value = self._selected_value("gear-editor:slot")
        if value:
            self.selected_slot = GearSlotCode(value)
        self._build()
        await interaction.response.edit_message(content=self.content, view=self)

    async def select_state(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        value = self._selected_value("gear-editor:state")
        if self.selected_slot is None or value is None:
            await interaction.response.send_message(
                "Choose a slot and state first.", ephemeral=True
            )
            return
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            if static.id != self.static_id:
                raise ValueError("This gear editor is stale.")
            character = session.scalar(
                select(Character).where(
                    Character.id == self.character_id,
                    Character.active.is_(True),
                    Character.static_member_id == self.member_id,
                    Character.static_member.has(active=True),
                    Character.static_member.has(static_id=self.static_id),
                )
            )
            slot = session.scalar(select(GearSlot).where(GearSlot.code == self.selected_slot))
            if character is None or slot is None:
                raise ValueError("This gear editor is stale.")
            set_gear(
                session,
                static,
                character,
                slot,
                GearClassification(value),
                interaction.user.id,
            )
        self._build(
            f"Saved {SLOT_LABEL[self.selected_slot.value]} as "
            f"{STATE_LABELS[GearClassification(value)]}."
        )
        await interaction.response.edit_message(content=self.content, view=self)

    async def reset_slot(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        if self.selected_slot is None:
            await interaction.response.send_message("Choose a slot first.", ephemeral=True)
            return
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            if static.id != self.static_id:
                raise ValueError("This gear editor is stale.")
            character = session.scalar(
                select(Character).where(
                    Character.id == self.character_id,
                    Character.static_member_id == self.member_id,
                    Character.static_member.has(static_id=self.static_id),
                )
            )
            slot = session.scalar(select(GearSlot).where(GearSlot.code == self.selected_slot))
            row = session.scalar(
                select(CharacterGearSlot).where(
                    CharacterGearSlot.character_id == self.character_id,
                    CharacterGearSlot.gear_slot_id == slot.id,
                )
            )
            if row is not None:
                clear_gear(session, static, character, slot, interaction.user.id)
        self._build(f"Cleared {SLOT_LABEL[self.selected_slot.value]}.")
        await interaction.response.edit_message(content=self.content, view=self)

    async def close(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        self.closed = True
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        self.stop()
        await interaction.response.edit_message(
            content=f"{self.content}\nEditor closed.", view=self
        )
