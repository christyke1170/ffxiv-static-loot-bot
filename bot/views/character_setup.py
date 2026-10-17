"""Member-owned Main/Alt registration with one form and atomic confirmation."""

import discord
from sqlalchemy import select

from app.models import CharacterKind, Job, StaticMember
from bot.checks import is_raid_leader
from bot.services.admin import add_character
from bot.services.commands import command_session
from bot.views.bis import BisMessageView
from bot.views.setup_base import SetupView


class CharacterPairModal(discord.ui.Modal, title="Main and Alt characters"):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.job_id = view.job_id
        self.fields = []
        for index, label in enumerate(("Main name", "Main world", "Alt name", "Alt world")):
            field = discord.ui.TextInput(
                label=label,
                required=True,
                max_length=100 if index % 2 == 0 else 50,
                default=view.details[index] or None,
            )
            self.fields.append(field)
            self.add_item(field)

    async def on_submit(self, interaction):
        if not await self.view.interaction_check(interaction):
            return
        values = tuple((field.value or "").strip() for field in self.fields)
        if not all(values):
            await interaction.response.send_message(
                "Every name and world is required.", ephemeral=True
            )
            return
        if values[:2] == values[2:]:
            await interaction.response.send_message(
                "Main and Alt must be different characters.", ephemeral=True
            )
            return
        self.view.details = values
        self.view.job_id = self.job_id
        self.view.build_preview()
        await interaction.response.edit_message(view=self.view)


class CharacterSetupView(SetupView):
    def __init__(self, bot, static_id, member_id, owner_id, guild_id):
        super().__init__(bot, static_id, owner_id, guild_id, leader_only=False)
        self.member_id = member_id
        self.page = 0
        self.job_id = None
        self.details = ("", "", "", "")
        with command_session(bot) as session:
            member = session.get(StaticMember, member_id)
            self.member_name = member.display_name
            self.jobs = list(session.scalars(select(Job).order_by(Job.abbreviation)))
            existing = {row.kind: row for row in member.characters if row.active}
            main, alt = existing.get(CharacterKind.MAIN), existing.get(CharacterKind.ALT)
            self.details = (
                main.name if main else "",
                main.world if main else "",
                alt.name if alt else "",
                alt.world if alt else "",
            )
        if not self.jobs:
            raise ValueError("Run `/setup seed` before registering characters.")
        self.build()

    def target_member(self, session, interaction):
        static = self.target_static(session, interaction)
        member = session.get(StaticMember, self.member_id)
        if member is None or member.static_id != static.id or not member.active:
            raise ValueError("This member is no longer active in the selected static.")
        if member.discord_user_id != interaction.user.id and not is_raid_leader(interaction, None):
            raise ValueError("Raid-leader permission is required to register for another member.")
        return member

    async def interaction_check(self, interaction):
        if not await super().interaction_check(interaction):
            return False
        try:
            with command_session(self.bot) as session:
                self.target_member(session, interaction)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return False
        return True

    def build(self):
        self.clear_items()
        self.text(
            f"**Register Main / Alt for {discord.utils.escape_markdown(self.member_name)}**\n"
            "Choose the shared job, then enter both characters in one form. "
            "Nothing is written until you confirm the preview. "
            "Existing characters are retained; use `/character edit` to change them."
        )
        self.picker = discord.ui.Select(
            placeholder="Shared Main / Alt job",
            options=[
                discord.SelectOption(label=f"{job.abbreviation} - {job.name}", value=str(job.id))
                for job in self.jobs[self.page * 25 : (self.page + 1) * 25]
            ],
        )
        self.picker.callback = self.choose_job
        self.add_item(discord.ui.ActionRow(self.picker))
        self.add_item(
            discord.ui.ActionRow(
                self.button("Previous jobs", self.previous, disabled=self.page == 0),
                self.button(
                    "Next jobs", self.next, disabled=(self.page + 1) * 25 >= len(self.jobs)
                ),
                self.button("Cancel", self.cancel),
            )
        )

    async def choose_job(self, interaction):
        self.job_id = int(self.picker.values[0])
        await interaction.response.send_modal(CharacterPairModal(self))

    def build_preview(self):
        self.clear_items()
        job = next(job for job in self.jobs if job.id == self.job_id)
        main_name, main_world, alt_name, alt_world = map(
            discord.utils.escape_markdown, self.details
        )
        self.text(
            f"**Review Main / Alt - {job.abbreviation}**\n"
            f"Main: {main_name}@{main_world}\nAlt: {alt_name}@{alt_world}\n"
            "New characters start with Crafted / EX gear and the appropriate Offhand state. "
            "Existing matching characters and their gear are preserved."
        )
        self.add_item(
            discord.ui.ActionRow(
                self.button("Save pair", self.save),
                self.button("Edit details", self.edit),
                self.button("Change job", self.change_job),
                self.button("Cancel", self.cancel),
            )
        )

    async def edit(self, interaction):
        await interaction.response.send_modal(CharacterPairModal(self))

    async def change_job(self, interaction):
        self.build()
        await interaction.response.edit_message(view=self)

    async def save(self, interaction):
        if not await self.interaction_check(interaction):
            return
        if self.job_id is None or not all(self.details):
            await interaction.response.send_message(
                "Complete and review the form first.", ephemeral=True
            )
            return
        try:
            with command_session(self.bot) as session:
                member = self.target_member(session, interaction)
                job = session.get(Job, self.job_id)
                if job is None:
                    raise ValueError("That job is no longer available. Reopen setup.")
                for kind, name, world in (
                    (CharacterKind.MAIN, *self.details[:2]),
                    (CharacterKind.ALT, *self.details[2:]),
                ):
                    existing = [row for row in member.characters if row.active and row.kind is kind]
                    if existing:
                        if len(existing) != 1 or (
                            existing[0].name,
                            existing[0].world,
                            existing[0].job_id,
                        ) != (name, world, job.id):
                            raise ValueError(
                                f"An active {kind.value.title()} already exists with "
                                "different details. Use `/character edit` to change it."
                            )
                    else:
                        add_character(session, member, name, world, kind, job.abbreviation)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(view=BisMessageView("Main / Alt pair saved."))

    async def cancel(self, interaction):
        self.stop()
        await interaction.response.edit_message(
            view=BisMessageView("Character setup cancelled; no changes were written.")
        )

    async def previous(self, interaction):
        self.page -= 1
        self.build()
        await interaction.response.edit_message(view=self)

    async def next(self, interaction):
        self.page += 1
        self.build()
        await interaction.response.edit_message(view=self)
