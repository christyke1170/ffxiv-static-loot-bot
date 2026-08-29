import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from app.models import CharacterGearSlot, StaticMember
from app.services.gear import (
    clear_gear,
    import_current_state,
    set_manual_completion,
)
from app.services.needs_formatting import format_needs_player
from app.services.needs_v2 import calculate_character_needs_v2
from bot.checks import is_raid_leader, require_raid_leader
from bot.services.commands import command_session, defer, read_json_attachment, reply, selected
from bot.services.gear import character, member_character, slot
from bot.views.gear import GearEditorView


class Gear(commands.Cog):
    group = app_commands.Group(name="gear", description="Manage current character gear")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="set")
    @require_raid_leader(None)
    @app_commands.choices(
        main_or_alt=[
            app_commands.Choice(name="Main", value="MAIN"),
            app_commands.Choice(name="Alt", value="ALT"),
        ]
    )
    async def set(self, interaction, display_name: str, main_or_alt: str):
        if not is_raid_leader(interaction, None):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            member, target = member_character(session, static, display_name, main_or_alt)
            static_id, member_id, target_id = static.id, member.id, target.id
        view = GearEditorView(
            self.bot,
            static_id,
            member_id,
            target_id,
            interaction.user.id,
            interaction.guild.id,
        )
        await interaction.response.send_message(content=view.content, view=view, ephemeral=True)

    @set.autocomplete("display_name")
    async def set_display_name_autocomplete(self, interaction, current: str):
        if not is_raid_leader(interaction, None):
            return []
        try:
            with command_session(self.bot) as session:
                static = selected(session, interaction)
                members = list(
                    session.scalars(
                        select(StaticMember)
                        .where(
                            StaticMember.static_id == static.id,
                            StaticMember.active.is_(True),
                            StaticMember.display_name.ilike(f"%{current.strip()}%"),
                        )
                        .order_by(StaticMember.display_name, StaticMember.id)
                    )
                )
        except ValueError:
            return []
        return [
            app_commands.Choice(name=member.display_name[:100], value=str(member.id))
            for member in members[:25]
        ]

    @group.command(name="clear")
    @require_raid_leader(None)
    async def clear(self, interaction, character_name: str, gear_slot: str):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = character(session, static, character_name)
            target_slot = slot(session, gear_slot)
            clear_gear(session, static, target, target_slot, interaction.user.id)
        await reply(interaction, "Current gear cleared; audit history retained.", ephemeral=True)

    @group.command(name="complete")
    @require_raid_leader(None)
    async def complete(
        self,
        interaction,
        character_name: str,
        gear_slot: str,
        complete: bool,
        reason: str | None = None,
    ):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = character(session, static, character_name)
            target_slot = slot(session, gear_slot)
            existing = session.scalar(
                select(CharacterGearSlot).where(
                    CharacterGearSlot.character_id == target.id,
                    CharacterGearSlot.gear_slot_id == target_slot.id,
                )
            )
            before = existing.manually_complete if existing else None
            set_manual_completion(
                session, static, target, target_slot, complete, interaction.user.id, reason
            )
        await reply(
            interaction,
            f"Manual completion "
            f"{'unchanged' if before == complete else 'updated'}: "
            f"{'set' if complete else 'unset'}.",
            ephemeral=True,
        )

    @group.command(name="show")
    async def show(self, interaction, character_name: str):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = character(session, static, character_name)
            result = calculate_character_needs_v2(session, target.id)
        await reply(interaction, format_needs_player(result))

    @group.command(name="import")
    @require_raid_leader(None)
    async def import_state(self, interaction, attachment: discord.Attachment):
        await defer(interaction, ephemeral=True)
        data = await read_json_attachment(attachment)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            import_current_state(session, static, data, interaction.user.id, dry_run=True)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            counts = import_current_state(session, static, data, interaction.user.id)
        await reply(
            interaction,
            f"Imported {counts.characters} characters, {counts.gear_slots} gear slots, "
            f"{counts.inventory_items} inventory items, {counts.book_balances} book balances, "
            f"and {counts.augmentation_materials} material balances.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Gear(bot))
