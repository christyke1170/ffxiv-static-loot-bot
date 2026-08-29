"""Ephemeral classification-only current gear editor."""

import discord
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import (
    Character,
    CharacterGearSlot,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Static,
    V2ResourceBalance,
)
from app.services.formatting import SLOT_LABEL
from app.services.gear import clear_gear, set_gear
from app.services.neutral_resources import set_current_balance
from bot.checks import is_raid_leader
from bot.services.commands import command_session, selected

GEAR_EDITOR_TIMEOUT = 600.0
MAX_COMPONENTS = 40
MAX_MODAL_INPUTS = 5
MAX_BOOK_BALANCE = 1_000_000
STATE_LABELS = {
    GearClassification.CRAFTED_EX: "Crafted / EX",
    GearClassification.SAVAGE: "Savage",
    GearClassification.TOME: "Tome",
    GearClassification.AUGMENTED_TOME: "Augmented Tome",
    GearClassification.GARBAGE: "Garbage",
}
SLOT_LABELS = {
    GearSlotCode.WEAPON: "Weapon",
    GearSlotCode.HEAD: "Hat",
    GearSlotCode.BODY: "Chest",
    GearSlotCode.HANDS: "Gloves",
    GearSlotCode.LEGS: "Pants",
    GearSlotCode.FEET: "Boots",
    GearSlotCode.EARRINGS: "Earring",
    GearSlotCode.NECKLACE: "Necklace",
    GearSlotCode.BRACELETS: "Bracelet",
    GearSlotCode.RING_1: "Ring 1",
    GearSlotCode.RING_2: "Ring 2",
    GearSlotCode.OFFHAND: "Offhand",
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
                for floor_id, value in desired.items():
                    set_current_balance(session, static, character, f"BOOK_FLOOR_{floor_id}", value)
        except ValueError:
            await interaction.response.send_message(
                "You cannot use this gear editor.", ephemeral=True
            )
            return
        self.editor._build()
        await interaction.response.edit_message(view=self.editor)


class GearMessageView(discord.ui.LayoutView):
    def __init__(self, text: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text)))


class GearEditorView(discord.ui.LayoutView):
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
        self.categories: dict[GearSlotCode, GearClassification] = {}
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
            balances = {
                int(row.resource_key.removeprefix("BOOK_FLOOR_")): row.quantity
                for row in session.scalars(
                    select(V2ResourceBalance).where(
                        V2ResourceBalance.static_id == self.static_id,
                        V2ResourceBalance.recipient_id == self.character_id,
                        V2ResourceBalance.resource_key.like("BOOK_FLOOR_%"),
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
                tuple((number, number, balances.get(number, 0)) for number in range(1, 5)),
            )

    def _build(self, notice: str | None = None) -> None:
        self.clear_items()
        name, kind, job, uses_offhand, slots, current, books = self._load()
        offhand_not_applicable = not uses_offhand
        slot_controls = []
        for slot in slots:
            value = self.categories.get(slot.code, current.get(slot.id))
            if slot.code is GearSlotCode.OFFHAND and offhand_not_applicable:
                value = GearClassification.NOT_APPLICABLE
            self.categories[slot.code] = value
            choices = [(None, "Missing"), *STATE_LABELS.items()]
            if slot.code is GearSlotCode.OFFHAND and offhand_not_applicable:
                choices = [(GearClassification.NOT_APPLICABLE, "N/A")]
            elif slot.code is GearSlotCode.OFFHAND:
                choices.append((GearClassification.NOT_APPLICABLE, "N/A"))
            control = discord.ui.Select(
                placeholder=f"{SLOT_LABELS[slot.code]}: {STATE_LABELS.get(value, 'Missing')}",
                options=[
                    discord.SelectOption(
                        label=label,
                        value=classification.value if classification else "MISSING",
                        default=classification is value,
                    )
                    for classification, label in choices
                ],
                custom_id=f"gear-editor:slot:{slot.code.value}",
                disabled=slot.code is GearSlotCode.OFFHAND and offhand_not_applicable,
            )
            control.callback = self._select_category(slot.code)
            slot_controls.append(control)
        save = discord.ui.Button(
            label="Save", style=discord.ButtonStyle.success, custom_id="gear-editor:save"
        )
        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.secondary, custom_id="gear-editor:cancel"
        )
        adjust_books = discord.ui.Button(label="Adjust Books", custom_id="gear-editor:adjust-books")
        adjust_books.callback = self.adjust_books
        save.callback = self.save
        cancel.callback = self.cancel
        selected_label = "Edit all gear slots"
        self.content = (
            f"**{discord.utils.escape_markdown(name)} - {kind} - {job}**\n"
            f"{selected_label}\n\n"
            "**Books**\n"
            + (
                "\n".join(f"Floor {number}: {available}" for _, number, available in books)
                or "None"
            )
        )
        if notice:
            self.content += f"\n{notice}"
        controls = []
        for index, (slot, control) in enumerate(zip(slots[:-1], slot_controls[:-1], strict=True)):
            label = SLOT_LABELS[slot.code]
            if index == 0:
                label = f"{self.content}\n\n{label}"
            controls.extend((discord.ui.TextDisplay(label), discord.ui.ActionRow(control)))
        last_slot = slots[-1]
        controls.extend(
            (
                discord.ui.TextDisplay(SLOT_LABELS[last_slot.code]),
                discord.ui.ActionRow(slot_controls[-1]),
                discord.ui.ActionRow(adjust_books, save, cancel),
            )
        )
        for control in controls:
            self.add_item(control)
        assert self.total_children_count <= MAX_COMPONENTS

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

    def _select_category(self, slot_code: GearSlotCode):
        async def callback(interaction: discord.Interaction) -> None:
            if not await self._authorized(interaction):
                return
            value = self._selected_value(f"gear-editor:slot:{slot_code.value}")
            self.categories[slot_code] = None if value == "MISSING" else GearClassification(value)
            self._build()
            await interaction.response.edit_message(view=self)

        return callback

    async def save(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        with command_session(self.bot) as session:
            static, character = self._validated_target(session, interaction)
            slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
            missing = [SLOT_LABELS[slot.code] for slot in slots if slot.code not in self.categories]
            if missing:
                raise ValueError("Choose a current state for: " + ", ".join(missing))
            for target_slot in slots:
                classification = self.categories[target_slot.code]
                existing = session.scalar(
                    select(CharacterGearSlot).where(
                        CharacterGearSlot.character_id == character.id,
                        CharacterGearSlot.gear_slot_id == target_slot.id,
                    )
                )
                if classification is None:
                    if existing is not None:
                        clear_gear(session, static, character, target_slot, interaction.user.id)
                elif existing is None or existing.current_classification is not classification:
                    set_gear(
                        session, static, character, target_slot, classification, interaction.user.id
                    )
        self.stop()
        await interaction.response.edit_message(
            view=GearMessageView("Current gear saved; audit history retained.")
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        self.stop()
        await interaction.response.edit_message(
            view=GearMessageView("Gear editor cancelled; no changes were written.")
        )

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
        await interaction.response.edit_message(view=self)
