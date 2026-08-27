from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import (
    CharacterAugmentationInventory,
    CharacterFloorBookBalance,
    GearClassification,
    InventoryItem,
)
from app.services.gear import set_augmentation_material, set_books, set_inventory
from bot.checks import require_raid_leader
from bot.services.commands import command_session, defer, reply, selected
from bot.services.gear import character, floor, material


class Inventory(commands.Cog):
    group = app_commands.Group(name="inventory", description="Manage unequipped gear categories")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="set")
    @require_raid_leader(None)
    async def set(
        self,
        interaction,
        character_name: str,
        gear_slot: str,
        category: str,
        quantity: int,
    ):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = character(session, static, character_name)
            from bot.services.gear import slot

            target_slot = slot(session, gear_slot)
            try:
                classification = GearClassification[category.upper()]
            except KeyError as exc:
                raise ValueError("Unknown gear category.") from exc
            existing = session.scalar(
                select(InventoryItem).where(
                    InventoryItem.character_id == target.id,
                    InventoryItem.gear_slot_id == target_slot.id,
                    InventoryItem.classification == classification,
                )
            )
            before = existing.quantity if existing else None
            set_inventory(
                session,
                static,
                target,
                target_slot,
                classification,
                quantity,
                interaction.user.id,
            )
            action = (
                "cleared"
                if quantity == 0 and before is not None
                else "unchanged"
                if before == quantity or (quantity == 0 and before is None)
                else "created"
                if before is None
                else "updated"
            )
        await reply(interaction, f"Inventory {action}: quantity is {quantity}.", ephemeral=True)


class Augment(commands.Cog):
    group = app_commands.Group(name="augment", description="Manage augmentation materials")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="set")
    @require_raid_leader(None)
    async def set(self, interaction, character_name: str, material_code: str, quantity: int):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = character(session, static, character_name)
            target_material = material(session, static, material_code)
            existing = session.scalar(
                select(CharacterAugmentationInventory).where(
                    CharacterAugmentationInventory.character_id == target.id,
                    CharacterAugmentationInventory.augmentation_material_type_id
                    == target_material.id,
                )
            )
            before = existing.quantity if existing else None
            row = set_augmentation_material(
                session,
                static,
                target,
                target_material,
                quantity,
                interaction.user.id,
            )
            action = (
                "created" if before is None else "unchanged" if before == quantity else "updated"
            )
        await reply(
            interaction,
            f"Augmentation material {action}: {row.augmentation_material_type.name} "
            f"quantity is {quantity}.",
            ephemeral=True,
        )


class Books(commands.Cog):
    group = app_commands.Group(name="books", description="Manage Savage book balances")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="set")
    @require_raid_leader(None)
    async def set(
        self,
        interaction,
        character_name: str,
        floor_number: int,
        earned: int,
        spent: int,
        manual_adjustment: int = 0,
    ):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = character(session, static, character_name)
            target_floor = floor(session, static, floor_number)
            existing = session.scalar(
                select(CharacterFloorBookBalance).where(
                    CharacterFloorBookBalance.character_id == target.id,
                    CharacterFloorBookBalance.raid_floor_id == target_floor.id,
                )
            )
            before = (
                (
                    existing.earned,
                    existing.spent,
                    existing.manual_adjustment,
                )
                if existing
                else None
            )
            row = set_books(
                session,
                static,
                target,
                target_floor,
                earned,
                spent,
                manual_adjustment,
                interaction.user.id,
            )
            available = row.available
            after = (earned, spent, manual_adjustment)
            action = "created" if before is None else "unchanged" if before == after else "updated"
        await reply(
            interaction,
            f"Book balance {action}. Effective available: {available}.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Inventory(bot))
    await bot.add_cog(Augment(bot))
    await bot.add_cog(Books(bot))
