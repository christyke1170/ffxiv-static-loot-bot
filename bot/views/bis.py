"""Owner-restricted interactive Static + Job BiS category editor."""

import discord
from sqlalchemy import select

from app.models import GearClassification, GearSlot, GearSlotCode, Job, Static
from bot.checks import is_raid_leader
from bot.services.bis import clear_bis, load_bis, save_bis, validate_categories
from bot.services.commands import command_session, selected

LABELS = {
    GearClassification.CRAFTED_EX: "CRAFTED_EX",
    GearClassification.TOME: "TOME",
    GearClassification.AUGMENTED_TOME: "AUGMENTED_TOME",
    GearClassification.SAVAGE: "SAVAGE",
    GearClassification.NOT_APPLICABLE: "NOT_APPLICABLE",
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


class BisMessageView(discord.ui.LayoutView):
    def __init__(self, text: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(text)))


class BisEditorView(discord.ui.LayoutView):
    def __init__(self, bot, static_id: int, job_id: int, owner_id: int, guild_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.static_id = static_id
        self.job_id = job_id
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.categories: dict[GearSlotCode, GearClassification] = {}
        self.content = ""
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild is None
            or interaction.guild.id != self.guild_id
            or interaction.user.id != self.owner_id
            or not is_raid_leader(interaction, None)
        ):
            await interaction.response.send_message(
                "You cannot use this BiS editor.", ephemeral=True
            )
            return False
        try:
            with command_session(self.bot) as session:
                static = selected(session, interaction)
                if static.id != self.static_id:
                    raise ValueError
        except ValueError:
            await interaction.response.send_message("This BiS editor is stale.", ephemeral=True)
            return False
        return True

    def _load(self):
        with command_session(self.bot) as session:
            static = session.get(Static, self.static_id)
            job = session.get(Job, self.job_id)
            if static is None or job is None:
                raise ValueError("This BiS editor is stale.")
            row = load_bis(session, self.static_id, self.job_id)
            slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
            current = {item.gear_slot_id: item.classification for item in row.items} if row else {}
            return static.name, job.abbreviation, job.uses_offhand, slots, current

    def _build(self, notice: str | None = None) -> None:
        self.clear_items()
        static_name, job_name, uses_offhand, slots, current = self._load()
        controls = []
        for slot in slots:
            default = (
                GearClassification.NOT_APPLICABLE
                if slot.code is GearSlotCode.OFFHAND and not uses_offhand
                else GearClassification.CRAFTED_EX
            )
            value = self.categories.get(slot.code, current.get(slot.id, default))
            self.categories[slot.code] = value
            if slot.code is GearSlotCode.OFFHAND and not uses_offhand:
                value = GearClassification.NOT_APPLICABLE
                self.categories[slot.code] = value
            options = [
                discord.SelectOption(label=label, value=category.value, default=category is value)
                for category, label in LABELS.items()
                if uses_offhand
                or slot.code is not GearSlotCode.OFFHAND
                or category is GearClassification.NOT_APPLICABLE
            ]
            control = discord.ui.Select(
                placeholder=f"{SLOT_LABELS[slot.code]}: {value.value}",
                options=options,
                custom_id=f"bis-editor:slot:{slot.code.value}",
            )
            control.callback = self._select_callback(slot.code)
            controls.append(discord.ui.ActionRow(control))
        save = discord.ui.Button(
            label="Save", style=discord.ButtonStyle.success, custom_id="bis-editor:save"
        )
        save.callback = self.save
        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.secondary, custom_id="bis-editor:cancel"
        )
        cancel.callback = self.cancel
        controls.append(discord.ui.ActionRow(save, cancel))
        self.content = (
            f"**Static:** {static_name}\n**Job:** {job_name}\n"
            "Choose the desired category for every slot. Saving updates all Main and Alt "
            "characters of this job automatically." + (f"\n{notice}" if notice else "")
        )
        self.add_item(
            discord.ui.Container(
                *[
                    child
                    for slot, row in zip(slots, controls[:-1], strict=True)
                    for child in (
                        discord.ui.TextDisplay(SLOT_LABELS[slot.code]),
                        row,
                    )
                ],
                controls[-1],
            )
        )

    def _select_callback(self, slot_code):
        async def callback(interaction):
            value = next(
                child.values[0]
                for child in self.walk_children()
                if getattr(child, "custom_id", None) == f"bis-editor:slot:{slot_code.value}"
            )
            self.categories[slot_code] = GearClassification(value)
            self._build()
            await interaction.response.edit_message(view=self)

        return callback

    async def save(self, interaction: discord.Interaction) -> None:
        try:
            with command_session(self.bot) as session:
                static = selected(session, interaction)
                if static.id != self.static_id:
                    raise ValueError("This BiS editor is stale.")
                job = session.get(Job, self.job_id)
                slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
                categories = {
                    slot.code: self.categories.get(slot.code, GearClassification.NOT_APPLICABLE)
                    for slot in slots
                }
                validate_categories(session, job, categories)
                save_bis(session, static, job, categories, interaction.user.id)
        except ValueError as error:
            await interaction.response.edit_message(view=BisMessageView(str(error)))
            self.stop()
            return
        self.stop()
        await interaction.response.edit_message(
            view=BisMessageView(f"Saved BiS configuration for {job.abbreviation}.")
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            view=BisMessageView("BiS editor cancelled; no changes were written.")
        )

    async def on_timeout(self) -> None:
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        message = getattr(self, "message", None)
        if message is not None:
            await message.edit(view=self)


class BisClearView(discord.ui.LayoutView):
    def __init__(self, bot, static_id: int, job_id: int, owner_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.static_id = static_id
        self.job_id = job_id
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.content = "Clear this Static + Job BiS? This will not change current gear."
        confirm = discord.ui.Button(
            label="Confirm clear", style=discord.ButtonStyle.danger, custom_id="bis-clear:confirm"
        )
        confirm.callback = self.confirm
        cancel = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.secondary, custom_id="bis-clear:cancel"
        )
        cancel.callback = self.cancel
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(self.content),
                discord.ui.ActionRow(confirm, cancel),
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild is None
            or interaction.guild.id != self.guild_id
            or interaction.user.id != self.owner_id
            or not is_raid_leader(interaction, None)
        ):
            await interaction.response.send_message(
                "You cannot use this BiS confirmation.", ephemeral=True
            )
            return False
        try:
            with command_session(self.bot) as session:
                static = selected(session, interaction)
                if static.id != self.static_id:
                    raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "This BiS confirmation is stale.", ephemeral=True
            )
            return False
        return True

    async def confirm(self, interaction: discord.Interaction) -> None:
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            if static.id != self.static_id:
                raise ValueError("This BiS confirmation is stale.")
            job = session.get(Job, self.job_id)
            if job is None:
                raise ValueError("This BiS confirmation is stale.")
            changed = clear_bis(session, static, job, interaction.user.id)
        self.stop()
        await interaction.response.edit_message(
            view=BisMessageView(
                f"Cleared {job.abbreviation} BiS for {static.name}. "
                "Matching characters now report missing job BiS."
                if changed
                else "BiS was already clear; no changes were written."
            )
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            view=BisMessageView("BiS clear cancelled; no changes were written.")
        )

    async def on_timeout(self) -> None:
        for item in self.walk_children():
            if hasattr(item, "disabled"):
                item.disabled = True
        message = getattr(self, "message", None)
        if message is not None:
            await message.edit(view=self)
