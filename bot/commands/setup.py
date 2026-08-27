from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from app.models import GearSlot, Job
from app.services.seed import seed_reference_data
from bot.checks import require_bot_admin
from bot.services.commands import command_session, defer, reply, selected
from bot.services.demo import create_demo_static, refresh_demo_static
from bot.services.migrations import migration_head


class Setup(commands.Cog):
    setup = app_commands.Group(name="setup", description="Bot administration")

    def __init__(self, bot):
        self.bot = bot

    @setup.command(name="status")
    async def status(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            guild_id = interaction.guild.id if interaction.guild else "DM"
            try:
                current = (
                    session.connection()
                    .exec_driver_sql("SELECT version_num FROM alembic_version")
                    .scalar()
                )
                db = "connected"
            except Exception:
                current, db = "unavailable", "unavailable"
            slots = session.scalar(select(func.count()).select_from(GearSlot))
            jobs = session.scalar(select(func.count()).select_from(Job))
            statics = 0
            if interaction.guild:
                from bot.services.admin import list_statics

                statics = len(list_statics(session, interaction.guild.id))
        expected = migration_head()
        sync = (
            f"development guild ({self.bot.settings.dev_guild_id})"
            if self.bot.settings.dev_guild_id
            else "global"
        )
        await reply(
            interaction,
            f"Version: 0.1.0\nDatabase: {db}\nMigration: {current} / {expected}\n"
            f"Seed records: jobs {jobs}/21, gear slots {slots}/12\n"
            f"Guild: {guild_id}\nSync: {sync}\nStatics: {statics}",
            ephemeral=True,
        )

    @setup.command(name="seed")
    @require_bot_admin(None)
    async def seed(self, interaction):
        await defer(interaction, ephemeral=True)
        with command_session(self.bot) as session:
            result = seed_reference_data(session)
        await reply(
            interaction,
            f"Seed complete. Jobs: {result.inserted_jobs} inserted, "
            f"{result.existing_jobs} existing. "
            f"Gear slots: {result.inserted_slots} inserted, {result.existing_slots} existing.",
            ephemeral=True,
        )

    @setup.command(name="demo", description="Create an isolated fictional eight-player demo")
    @require_bot_admin(None)
    async def demo(self, interaction):
        await defer(interaction, ephemeral=True)
        if interaction.guild is None:
            raise ValueError("This command can only be used in a Discord guild.")
        with command_session(self.bot) as session:
            result = create_demo_static(
                session,
                interaction.guild.id,
                interaction.guild.name,
                interaction.user.id,
            )
        await reply(
            interaction,
            f"Created isolated **{result.static_name}** (fictional demo data): "
            f"{result.member_count} active members, {result.character_count} active characters, "
            f"four-floor tier `{result.tier_code}`, {result.bis_set_count} complete BiS sets, "
            f"selected BiS for all characters, varied gear/resources, and hierarchy v"
            f"{result.hierarchy_version}. No reclear week was created. The demo is now selected.\n"
            "Next: `/gearboard`, `/needs floor`, then `/reclear setup` and `/reclear plan`.",
            ephemeral=True,
        )

    @setup.command(
        name="demo-refresh", description="Repair the selected verified fictional Loot Demo"
    )
    @require_bot_admin(None)
    async def demo_refresh(self, interaction):
        await defer(interaction, ephemeral=True)
        if interaction.guild is None:
            raise ValueError("This command can only be used in a Discord guild.")
        with command_session(self.bot) as session:
            result = refresh_demo_static(
                session,
                interaction.guild.id,
                interaction.user.id,
                selected(session, interaction),
            )
        await reply(
            interaction,
            f"Refreshed verified **{result.static_name}** in place. "
            f"Counts: created {result.created}, updated {result.updated}, "
            f"unchanged {result.unchanged}, rejected {result.rejected}. "
            "The static was not deleted or recreated.",
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Setup(bot))
