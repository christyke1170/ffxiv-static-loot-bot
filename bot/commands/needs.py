from discord import app_commands
from discord.ext import commands

from app.models import CharacterKind
from app.schemas.needs_v2 import NeedsV2Status
from app.services.formatting import safe_text
from app.services.needs_formatting import format_needs_player
from app.services.needs_v2 import calculate_character_needs_v2
from bot.services.commands import command_session, defer, reply, selected
from bot.services.gear import character


class Needs(commands.Cog):
    group = app_commands.Group(name="needs", description="Read current database-backed gear needs")

    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _characters(static):
        return sorted(
            (
                character
                for member in static.members
                if member.active
                for character in member.characters
                if character.active and character.kind is CharacterKind.MAIN
            ),
            key=lambda row: (row.static_member_id, row.id),
        )

    @staticmethod
    def _player_line(result):
        return (
            f"{safe_text(result.character_name)} ({safe_text(result.job_abbreviation or '?')}) â€” "
            f"{result.complete_slot_count}/{result.applicable_slot_count} applicable slots"
        )

    @classmethod
    def _warning_text(cls, results):
        warnings = tuple(
            dict.fromkeys(
                warning for result in results for warning in result.configuration_warnings
            )
        )
        return (
            "\nWarnings:\n" + "\n".join(f"- {safe_text(item)}" for item in warnings)
            if warnings
            else ""
        )

    @group.command(name="player")
    async def player(self, interaction, character_name: str):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            target = character(session, static, character_name)
            result = calculate_character_needs_v2(session, target.id)
        await reply(interaction, format_needs_player(result))

    @group.command(name="floor")
    async def floor(self, interaction, floor_number: int):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            results = tuple(
                calculate_character_needs_v2(session, row.id) for row in self._characters(static)
            )
            lines = []
            for result in results:
                for slot in result.slot_results:
                    if slot.required_floor_number == floor_number and slot.status not in {
                        NeedsV2Status.COMPLETE,
                        NeedsV2Status.MANUALLY_COMPLETE,
                        NeedsV2Status.NOT_APPLICABLE,
                    }:
                        lines.append(
                            f"{safe_text(result.character_name)} — {safe_text(slot.slot_name)} — "
                            f"{safe_text(slot.required_loot_type_code or 'configured need')}"
                        )
            warnings = self._warning_text(results)
        await reply(
            interaction,
            f"Floor {floor_number} configured loot needs\n"
            + ("\n".join(lines) or "No matching needs.")
            + warnings,
        )

    @group.command(name="augment")
    async def augment(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            results = tuple(
                calculate_character_needs_v2(session, row.id) for row in self._characters(static)
            )
            lines = [
                f"{self._player_line(result)} â€” "
                + (
                    ", ".join(
                        f"{safe_text(row.material_name)}: owned {row.owned}, "
                        f"allocated {row.allocated}, additionally needed {row.additional_needed}"
                        for row in result.material_needs
                    )
                    or "none"
                )
                for result in results
            ]
            warnings = self._warning_text(results)
        await reply(interaction, "Augmentation materials\n" + "\n".join(lines) + warnings)

    @group.command(name="books")
    async def books(self, interaction):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            results = tuple(
                calculate_character_needs_v2(session, row.id) for row in self._characters(static)
            )
            lines = [
                f"{self._player_line(result)} â€” "
                + ", ".join(
                    f"Floor {row.floor_number}: {row.available}" for row in result.book_balances
                )
                for result in results
            ]
            warnings = self._warning_text(results)
        await reply(
            interaction,
            "Book balances (informational; administrator spending is recorded manually)\n"
            + "\n".join(lines)
            + warnings,
        )


async def setup(bot):
    await bot.add_cog(Needs(bot))
