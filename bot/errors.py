"""Application exceptions and safe Discord-facing error handling."""

import logging

import discord

log = logging.getLogger(__name__)


class PermissionDeniedError(ValueError):
    pass


class StaleMigrationError(RuntimeError):
    pass


class DatabaseOperationError(RuntimeError):
    pass


async def handle_app_command_error(interaction: discord.Interaction, error: Exception) -> None:
    original = getattr(error, "original", error)
    if isinstance(original, PermissionDeniedError):
        message = "You do not have permission to use this command."
    elif isinstance(original, (ValueError, discord.app_commands.TransformerError)):
        message = str(original)[:500] or "The supplied input is invalid."
    elif isinstance(original, StaleMigrationError):
        message = "The database schema is out of date. Run `alembic upgrade head`."
    elif isinstance(original, DatabaseOperationError):
        message = "The database operation failed. Please try again later."
    else:
        log.exception("Unhandled Discord command error", exc_info=original)
        message = "An unexpected internal error occurred."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
