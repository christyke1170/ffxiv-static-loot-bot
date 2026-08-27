"""Discord client and extension loading."""

import asyncio
import logging

import discord
from discord.ext import commands

from app.config import Settings
from app.database import create_database_engine, create_session_factory
from bot.errors import handle_app_command_error

log = logging.getLogger(__name__)


class StaticLootClient(commands.Bot):
    def __init__(self, settings: Settings, **kwargs):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, **kwargs)
        self.settings = settings
        self.database_engine = create_database_engine(settings.database_url)
        self.session_factory = create_session_factory(self.database_engine)

    async def setup_hook(self) -> None:
        extensions = (
            "bot.commands.setup",
            "bot.commands.static",
            "bot.commands.member",
            "bot.commands.character",
            "bot.commands.tier",
            "bot.commands.bis",
            "bot.commands.hierarchy",
            "bot.commands.gear",
            "bot.commands.resources",
            "bot.commands.needs",
            "bot.commands.gearboard",
            "bot.commands.reclear",
            "bot.commands.loot",
            "bot.commands.lootboard",
        )
        loaded = []
        try:
            for extension in extensions:
                await self.load_extension(extension)
                loaded.append(extension)
        except Exception:
            for extension in reversed(loaded):
                await self.unload_extension(extension)
            raise
        from bot.views.confirmation import register_persistent_confirmation_views

        register_persistent_confirmation_views(self)
        asyncio.get_running_loop().set_exception_handler(self._task_exception_handler)
        self.tree.on_error = handle_app_command_error
        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Connected as %s", self.user)

    def _task_exception_handler(self, _loop, context) -> None:
        error = context.get("exception")
        log.error("Unhandled asynchronous task: %s", context.get("message"), exc_info=error)

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            self.database_engine.dispose()
