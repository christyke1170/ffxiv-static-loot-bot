import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import RaidTier
from app.services.imports import import_raid_tier
from bot.checks import require_raid_leader
from bot.services.admin import clear_tier, select_tier
from bot.services.commands import (
    command_session,
    defer,
    pages,
    read_json_attachment,
    reply,
    selected,
)


class Tier(commands.Cog):
    group = app_commands.Group(name="tier", description="Manage raid tiers")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="import")
    @require_raid_leader(None)
    async def import_tier(self, interaction, attachment: discord.Attachment):
        await defer(interaction, ephemeral=True)
        data = await read_json_attachment(attachment)
        with command_session(self.bot) as session:
            import_raid_tier(session, data, dry_run=True)
        with command_session(self.bot) as session:
            tier = import_raid_tier(session, data)
            result = tier.import_counts
            counts = (
                len(tier.floors),
                sum(len(f.loot_rules) for f in tier.floors),
                len(tier.augmentation_material_types),
            )
        await reply(
            interaction,
            f"Imported tier {discord.utils.escape_markdown(tier.name)}: "
            f"inserted {result.inserted}, updated {result.updated}, "
            f"unchanged {result.unchanged}, rejected {result.rejected}. "
            f"Definition has {counts[0]} floors, {counts[1]} loot rules, {counts[2]} materials."
            + (" Referenced history was retained unchanged." if result.rejected else ""),
            ephemeral=True,
        )

    @group.command(name="select")
    @require_raid_leader(None)
    async def select(self, interaction, tier: str):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            row = session.scalar(
                select(RaidTier).where((RaidTier.code == tier) | (RaidTier.name == tier))
            )
            if row is None:
                raise ValueError("Unknown raid tier.")
            change = select_tier(static, row)
            old = change.old.name if change.old else "none"
            status = "unchanged" if not change.changed else "replaced"
        await reply(
            interaction,
            f"Tier selection {status}: {discord.utils.escape_markdown(old)} → "
            f"{discord.utils.escape_markdown(row.name)}. Existing weekly snapshots were unchanged.",
            ephemeral=True,
        )

    @group.command(name="clear", description="Clear the active tier when no workflow depends on it")
    @require_raid_leader(None)
    async def clear(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            change = clear_tier(session, selected(session, interaction))
            old = change.old.name if change.old else "none"
        await reply(
            interaction,
            f"Tier selection {'cleared' if change.changed else 'unchanged'}: "
            f"{discord.utils.escape_markdown(old)} → none.",
            ephemeral=True,
        )

    @group.command(name="show")
    async def show(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            tier = selected(session, interaction).active_raid_tier
            if tier is None:
                raise ValueError("The selected static has no active tier.")

            def rule_text(rule):
                material = (
                    rule.augmentation_material_type.name
                    if rule.augmentation_material_type
                    else "none"
                )
                return (
                    f"{rule.loot_type.name} x{rule.expected_quantity} "
                    f"(book {rule.book_cost or 0}, material {material})"
                )

            lines = [
                f"{f.floor_number}: {f.name} — "
                + "; ".join(rule_text(rule) for rule in f.loot_rules)
                for f in sorted(tier.floors, key=lambda x: x.floor_number)
            ]
        for page in pages(lines):
            await reply(interaction, page)


async def setup(bot):
    await bot.add_cog(Tier(bot))
