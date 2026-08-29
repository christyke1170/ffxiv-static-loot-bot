from discord import app_commands
from discord.ext import commands

from app.models import GearClassification
from app.services.neutral_resources import (
    current_balance,
    set_current_balance,
    validate_resource_key,
)
from bot.checks import require_raid_leader
from bot.services.commands import command_session, defer, reply, selected
from bot.services.gear import character


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
            key = f"{classification.value}_{target_slot.code.value}"
            existing = current_balance(session, static.id, target.id, key)
            before = existing.quantity if existing else None
            set_current_balance(session, static, target, key, quantity)
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
            key = material_code.strip().upper()
            key = {"GLAZE": "ACCESSORY_GLAZE", "TWINE": "ARMOR_TWINE"}.get(key, key)
            validate_resource_key(key)
            existing = current_balance(session, static.id, target.id, key)
            before = existing.quantity if existing else None
            set_current_balance(session, static, target, key, quantity)
            action = (
                "created" if before is None else "unchanged" if before == quantity else "updated"
            )
        await reply(
            interaction,
            f"Augmentation material {action}: {key} quantity is {quantity}.",
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
            available = earned - spent + manual_adjustment
            key = f"BOOK_FLOOR_{floor_number}"
            existing = current_balance(session, static.id, target.id, key)
            before = existing.quantity if existing else None
            set_current_balance(session, static, target, key, available)
            after = available
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
