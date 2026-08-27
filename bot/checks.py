"""Reusable Discord permission checks."""

import discord
from discord import app_commands

from app.config import Settings
from bot.errors import PermissionDeniedError


def _role_ids(interaction: discord.Interaction) -> set[int]:
    user = interaction.user
    return {role.id for role in getattr(user, "roles", ())}


def _settings(interaction: discord.Interaction, settings: Settings | None) -> Settings:
    return settings or interaction.client.settings


def is_bot_admin(interaction: discord.Interaction, settings: Settings | None) -> bool:
    settings = _settings(interaction, settings)
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator) or bool(
        _role_ids(interaction) & set(settings.bot_admin_role_ids)
    )


def is_raid_leader(interaction: discord.Interaction, settings: Settings | None) -> bool:
    settings = _settings(interaction, settings)
    return is_bot_admin(interaction, settings) or bool(
        _role_ids(interaction) & set(settings.raid_leader_role_ids)
    )


def require_bot_admin(settings: Settings | None):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not is_bot_admin(interaction, settings):
            raise PermissionDeniedError
        return True

    return app_commands.check(predicate)


def require_raid_leader(settings: Settings | None):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not is_raid_leader(interaction, settings):
            raise PermissionDeniedError
        return True

    return app_commands.check(predicate)
