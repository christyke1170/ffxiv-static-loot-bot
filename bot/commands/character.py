from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from app.models import CharacterKind
from bot.checks import is_raid_leader
from bot.services.admin import (
    add_character,
    edit_character,
    resolve_member_character,
    set_character_active,
)
from bot.services.commands import command_session, defer, pages, reply, selected


class Character(commands.Cog):
    group = app_commands.Group(name="character", description="Manage characters")

    def __init__(self, bot):
        self.bot = bot

    @group.command(name="add")
    async def add(self, interaction, name: str, world: str, kind: str, job: str):
        await defer(interaction, ephemeral=True)
        try:
            character_kind = CharacterKind[kind.upper()]
        except KeyError as exc:
            raise ValueError("Character kind must be MAIN or ALT.") from exc
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            member = next(
                (
                    m
                    for m in static.members
                    if m.active and m.discord_user_id == interaction.user.id
                ),
                None,
            )
            if member is None:
                raise ValueError("You must be an active member of the selected static.")
            row = add_character(session, member, name, world, character_kind, job)
        await reply(
            interaction,
            f"Created {discord.utils.escape_markdown(row.name)} ({row.kind}).",
            ephemeral=True,
        )

    @group.command(
        name="edit", description="Correct a character while preserving all relationships"
    )
    async def edit(
        self,
        interaction,
        current_name: str,
        member: discord.Member | None = None,
        new_name: str | None = None,
        new_world: str | None = None,
        new_kind: Literal["MAIN", "ALT"] | None = None,
        new_job: str | None = None,
        clear_incompatible_bis: bool = False,
    ):
        await defer(interaction, ephemeral=True)
        target_user_id = member.id if member is not None else interaction.user.id
        if target_user_id != interaction.user.id and not is_raid_leader(interaction, None):
            raise ValueError(
                "Raid-leader permission is required to correct another member's character."
            )
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            owner, target = resolve_member_character(session, static, target_user_id, current_name)
            if not owner.active:
                raise ValueError("Character corrections require an active static membership.")
            row, cleared = edit_character(
                session,
                static,
                target,
                interaction.user.id,
                new_name=new_name,
                new_world=new_world,
                new_kind=CharacterKind[new_kind] if new_kind else None,
                new_job=new_job,
                clear_incompatible_bis=clear_incompatible_bis,
            )
        suffix = f" Cleared {cleared} incompatible BiS selection(s)." if cleared else ""
        await reply(
            interaction,
            f"Updated {discord.utils.escape_markdown(row.name)}@"
            f"{discord.utils.escape_markdown(row.world)} ({row.kind.value}, "
            f"{row.job.abbreviation}); relationships and history were retained.{suffix}",
            ephemeral=True,
        )

    @group.command(
        name="deactivate", description="Deactivate a character when no open workflow uses it"
    )
    async def deactivate(
        self, interaction, current_name: str, member: discord.Member | None = None
    ):
        await self._set_active(interaction, current_name, member, False)

    @group.command(name="reactivate", description="Reactivate a character without losing history")
    async def reactivate(
        self, interaction, current_name: str, member: discord.Member | None = None
    ):
        await self._set_active(interaction, current_name, member, True)

    async def _set_active(self, interaction, current_name, member, active):
        await defer(interaction, ephemeral=True)
        target_user_id = member.id if member is not None else interaction.user.id
        if target_user_id != interaction.user.id and not is_raid_leader(interaction, None):
            raise ValueError(
                "Raid-leader permission is required to change another member's character."
            )
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            _, target = resolve_member_character(session, static, target_user_id, current_name)
            row = set_character_active(
                session, static, target, active=active, actor_id=interaction.user.id
            )
        await reply(
            interaction,
            f"{discord.utils.escape_markdown(row.name)} "
            f"{'reactivated' if active else 'deactivated'}; relationships and history "
            "were retained.",
            ephemeral=True,
        )

    @group.command(name="list")
    async def list(
        self, interaction, member: discord.Member | None = None, kind: str | None = None
    ):
        await defer(interaction)
        with command_session(self.bot) as session:
            static = selected(session, interaction)
            members = [
                m for m in static.members if member is None or m.discord_user_id == member.id
            ]
            rows = [
                f"{c.name}@{c.world} â€” {c.job.abbreviation} â€” "
                f"{c.kind} â€” {'active' if c.active else 'inactive'}"
                for m in members
                for c in m.characters
                if kind is None or c.kind.value == kind.upper()
            ]
        for page in pages([discord.utils.escape_markdown(x) for x in rows]):
            await reply(interaction, page)


async def setup(bot):
    await bot.add_cog(Character(bot))
