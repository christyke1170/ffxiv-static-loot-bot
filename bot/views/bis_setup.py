"""All-job BiS progress, batch edits, and explicit reviewed copies."""

import discord
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import BisSet, BisSetItem, GearSlot, GearSlotCode, Job
from app.models import GearClassification as Category
from bot.services.bis import load_bis, save_bis, validate_categories
from bot.services.commands import command_session
from bot.services.job_icons import display_job, job_emoji
from bot.views.bis import SLOT_LABELS, BisEditorView
from bot.views.setup_base import SetupView, remember_selection


def saved_categories(session, static_id, job_id):
    row = load_bis(session, static_id, job_id)
    return {item.gear_slot.code: item.classification for item in row.items} if row else {}


def setup_jobs(session, static_id):
    """Load all-job progress in bulk, including jobs without roster characters."""
    jobs = list(session.scalars(select(Job).order_by(Job.abbreviation)))
    slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
    if not slots:
        raise ValueError("Run `/setup seed` before configuring BiS.")
    sets = {
        row.job_id: {item.gear_slot.code: item.classification for item in row.items}
        for row in session.scalars(
            select(BisSet)
            .where(BisSet.static_id == static_id, BisSet.active.is_(True))
            .options(selectinload(BisSet.items).joinedload(BisSetItem.gear_slot))
        )
    }
    configured = set()
    for job in jobs:
        try:
            validate_categories(session, job, sets.get(job.id, {}), slots=slots)
            configured.add(job.id)
        except ValueError:
            pass
    return jobs, configured


def job_options(jobs, configured, icons=None):
    return [
        discord.SelectOption(
            label=f"{job.abbreviation} - {'Configured' if job.id in configured else 'Missing'}",
            value=str(job.id),
            description=job.name,
            emoji=(icons or {}).get(job.abbreviation),
        )
        for job in jobs
    ]


class BisSetupView(SetupView):
    def __init__(self, bot, static_id, owner_id, guild_id, *, page=0, notice=None):
        super().__init__(bot, static_id, owner_id, guild_id)
        self.page = page
        with command_session(bot) as session:
            from app.models import Static

            name = session.get(Static, static_id).name
            self.jobs, self.configured = setup_jobs(session, static_id)
        if not self.jobs:
            raise ValueError("Run `/setup seed` before configuring BiS.")
        self.text(
            f"**{name} - BiS setup**\n"
            f"Configured: {len(self.configured)}/{len(self.jobs)} jobs.\n"
            "Select any job to edit its slot categories in batches.\n"
            + (notice or "All supported jobs are included, regardless of roster usage.")
        )
        page_jobs = self.jobs[page * 25 : (page + 1) * 25]
        picker = discord.ui.Select(
            placeholder="Configure a job",
            options=job_options(page_jobs, self.configured, getattr(bot, "job_emojis", {})),
        )
        picker.callback = self.edit_job
        self.picker = picker
        self.add_item(discord.ui.ActionRow(picker))
        self.add_item(
            discord.ui.ActionRow(
                self.button("Previous jobs", self.previous, disabled=page == 0),
                self.button("Next jobs", self.next, disabled=(page + 1) * 25 >= len(self.jobs)),
                self.button("Copy saved BiS", self.copy, disabled=not self.configured),
            )
        )

    async def edit_job(self, interaction):
        view = BisBatchView(
            self.bot, self.static_id, int(self.picker.values[0]), self.owner_id, self.guild_id
        )
        await interaction.response.edit_message(view=view)
        self.stop()

    async def show_page(self, interaction, page):
        view = BisSetupView(self.bot, self.static_id, self.owner_id, self.guild_id, page=page)
        await interaction.response.edit_message(view=view)
        self.stop()

    async def previous(self, interaction):
        await self.show_page(interaction, self.page - 1)

    async def next(self, interaction):
        await self.show_page(interaction, self.page + 1)

    async def copy(self, interaction):
        view = BisCopyView(self.bot, self.static_id, self.owner_id, self.guild_id)
        await interaction.response.edit_message(view=view)
        self.stop()


class BisBatchView(SetupView):
    def __init__(self, bot, static_id, job_id, owner_id, guild_id):
        super().__init__(bot, static_id, owner_id, guild_id)
        self.job_id = job_id
        with command_session(bot) as session:
            job = session.get(Job, job_id)
            self.job_name, self.uses_offhand = job.abbreviation, job.uses_offhand
            self.slots = list(session.scalars(select(GearSlot).order_by(GearSlot.sort_order)))
            self.original = saved_categories(session, static_id, job_id)
        self.categories = {
            slot.code: self.original.get(slot.code, Category.CRAFTED_EX) for slot in self.slots
        }
        if not self.uses_offhand:
            self.categories[GearSlotCode.OFFHAND] = Category.NOT_APPLICABLE
        self.selected_slots = []
        self.category = Category.SAVAGE
        self.build()

    def build(self):
        self.clear_items()
        self.text(
            f"**{display_job(self.bot, self.job_name)} BiS - unsaved draft**\n"
            "Choose slots and a category, then Apply to selected slots. Review before saving.\n\n"
            + "\n".join(
                f"{SLOT_LABELS[slot.code]}: {self.categories[slot.code].value}"
                for slot in self.slots
            )
        )
        options = [
            discord.SelectOption(
                label=SLOT_LABELS[slot.code],
                value=slot.code.value,
                default=slot.code.value in self.selected_slots,
            )
            for slot in self.slots
            if self.uses_offhand or slot.code is not GearSlotCode.OFFHAND
        ]
        self.slot_picker = discord.ui.Select(
            placeholder="Select one or more slots", options=options, max_values=len(options)
        )
        self.slot_picker.callback = self.choose_slots
        self.category_picker = discord.ui.Select(
            placeholder="Category for selected slots",
            options=[
                discord.SelectOption(
                    label=category.value.replace("_", " ").title(),
                    value=category.value,
                    default=category is self.category,
                )
                for category in (
                    Category.CRAFTED_EX,
                    Category.TOME,
                    Category.AUGMENTED_TOME,
                    Category.SAVAGE,
                )
            ],
        )
        self.category_picker.callback = self.choose_category
        self.add_item(discord.ui.ActionRow(self.slot_picker))
        self.add_item(discord.ui.ActionRow(self.category_picker))
        self.add_item(
            discord.ui.ActionRow(
                self.button("Apply to selected slots", self.apply),
                self.button("Save", self.save),
                self.button("Individual slots", self.individual),
                self.button("Cancel / all jobs", self.cancel),
            )
        )

    async def choose_slots(self, interaction):
        self.selected_slots = list(self.slot_picker.values)
        remember_selection(self.slot_picker)
        await interaction.response.defer()

    async def choose_category(self, interaction):
        self.category = Category(self.category_picker.values[0])
        remember_selection(self.category_picker)
        await interaction.response.defer()

    async def apply(self, interaction):
        if not self.selected_slots:
            await interaction.response.send_message("Choose at least one slot.", ephemeral=True)
            return
        for value in self.selected_slots:
            self.categories[GearSlotCode(value)] = self.category
        self.build()
        await interaction.response.edit_message(view=self)

    async def save(self, interaction):
        if not await self.interaction_check(interaction):
            return
        try:
            with command_session(self.bot) as session:
                static = self.target_static(session, interaction)
                if saved_categories(session, static.id, self.job_id) != self.original:
                    raise ValueError("This job's BiS changed. Reopen setup before saving.")
                save_bis(
                    session,
                    static,
                    session.get(Job, self.job_id),
                    self.categories,
                    interaction.user.id,
                )
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.back(interaction, f"Saved {display_job(self.bot, self.job_name)} BiS.")

    async def individual(self, interaction):
        view = BisEditorView(self.bot, self.static_id, self.job_id, self.owner_id, self.guild_id)
        view.categories.update(self.categories)
        view._build()
        await interaction.response.edit_message(view=view)
        self.stop()

    async def back(self, interaction, notice):
        view = BisSetupView(self.bot, self.static_id, self.owner_id, self.guild_id, notice=notice)
        await interaction.response.edit_message(view=view)
        self.stop()

    async def cancel(self, interaction):
        await self.back(interaction, "Draft discarded; no changes were written.")


class BisCopyView(SetupView):
    def __init__(self, bot, static_id, owner_id, guild_id):
        super().__init__(bot, static_id, owner_id, guild_id)
        with command_session(bot) as session:
            self.jobs, configured = setup_jobs(session, static_id)
            self.configured = [job for job in self.jobs if job.id in configured]
        self.page = 0
        self.source_page = 0
        self.source_id = None
        self.targets = set()
        self.review = None
        self.build()

    def build(self):
        self.clear_items()
        self.text(
            "**Copy saved BiS**\nChoose a source and destination jobs. "
            "Existing destination sets will be replaced only after Review and Save.\n"
            "Offhand is N/A for jobs without it. For PLD, an N/A source Offhand "
            "uses the source Weapon category."
        )
        if self.review:
            names = [
                display_job(self.bot, job.abbreviation)
                for job in self.jobs
                if job.id in self.targets
            ]
            categories = self.review[0]
            self.text(
                "**Review: "
                + ", ".join(names)
                + "**\n"
                + "\n".join(
                    f"{SLOT_LABELS[slot]}: {value.value}" for slot, value in categories.items()
                )
                + "\nOffhand will be adapted for each destination as described above."
            )
        source_jobs = self.configured[self.source_page * 25 : (self.source_page + 1) * 25]
        self.source_picker = discord.ui.Select(
            placeholder="Source saved job",
            options=[
                discord.SelectOption(
                    label=job.abbreviation,
                    value=str(job.id),
                    default=job.id == self.source_id,
                    emoji=job_emoji(self.bot, job.abbreviation),
                )
                for job in source_jobs
            ],
        )
        self.source_picker.callback = self.choose_source
        page_jobs = self.jobs[self.page * 25 : (self.page + 1) * 25]
        self.target_picker = discord.ui.Select(
            placeholder="Destination jobs",
            min_values=0,
            max_values=len(page_jobs),
            options=[
                discord.SelectOption(
                    label=job.abbreviation,
                    value=str(job.id),
                    default=job.id in self.targets,
                    emoji=job_emoji(self.bot, job.abbreviation),
                )
                for job in page_jobs
            ],
        )
        self.target_picker.callback = self.choose_targets
        self.add_item(discord.ui.ActionRow(self.source_picker))
        self.add_item(discord.ui.ActionRow(self.target_picker))
        self.add_item(
            discord.ui.ActionRow(
                self.button("Review", self.preview),
                self.button("Save copy", self.save),
                self.button("Cancel", self.cancel),
            )
        )
        if len(self.jobs) > 25:
            self.add_item(
                discord.ui.ActionRow(
                    self.button("Previous targets", self.previous, disabled=self.page == 0),
                    self.button(
                        "Next targets", self.next, disabled=(self.page + 1) * 25 >= len(self.jobs)
                    ),
                    self.button(
                        "Previous sources", self.previous_source, disabled=self.source_page == 0
                    ),
                    self.button(
                        "Next sources",
                        self.next_source,
                        disabled=(self.source_page + 1) * 25 >= len(self.configured),
                    ),
                )
            )

    async def choose_source(self, interaction):
        self.source_id = int(self.source_picker.values[0])
        self.review = None
        remember_selection(self.source_picker)
        await interaction.response.defer()

    async def choose_targets(self, interaction):
        self.targets.difference_update(
            job.id for job in self.jobs[self.page * 25 : (self.page + 1) * 25]
        )
        self.targets.update(map(int, self.target_picker.values))
        self.review = None
        remember_selection(self.target_picker)
        await interaction.response.defer()

    async def preview(self, interaction):
        if not self.source_id or not self.targets or self.source_id in self.targets:
            await interaction.response.send_message(
                "Choose a source and at least one different destination job.", ephemeral=True
            )
            return
        try:
            with command_session(self.bot) as session:
                source = saved_categories(session, self.static_id, self.source_id)
                validate_categories(session, session.get(Job, self.source_id), source)
                originals = {
                    job_id: saved_categories(session, self.static_id, job_id)
                    for job_id in self.targets
                }
        except ValueError as error:
            self.review = None
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        self.review = source, originals
        self.build()
        await interaction.response.edit_message(view=self)

    async def save(self, interaction):
        if not await self.interaction_check(interaction):
            return
        if self.review is None:
            await interaction.response.send_message(
                "Review the copy before saving.", ephemeral=True
            )
            return
        source, originals = self.review
        try:
            with command_session(self.bot) as session:
                static = self.target_static(session, interaction)
                if saved_categories(session, static.id, self.source_id) != source:
                    raise ValueError("Source BiS changed. Review again before saving.")
                for job_id, original in originals.items():
                    if saved_categories(session, static.id, job_id) != original:
                        raise ValueError("Destination BiS changed. Review again before saving.")
                    job = session.get(Job, job_id)
                    categories = dict(source)
                    categories[GearSlotCode.OFFHAND] = (
                        source[GearSlotCode.OFFHAND]
                        if job.uses_offhand
                        and source[GearSlotCode.OFFHAND] is not Category.NOT_APPLICABLE
                        else source[GearSlotCode.WEAPON]
                        if job.uses_offhand
                        else Category.NOT_APPLICABLE
                    )
                    save_bis(session, static, job, categories, interaction.user.id)
        except ValueError as error:
            self.review = None
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.back(interaction, f"Copied BiS to {len(originals)} jobs.")

    async def back(self, interaction, notice):
        view = BisSetupView(self.bot, self.static_id, self.owner_id, self.guild_id, notice=notice)
        await interaction.response.edit_message(view=view)
        self.stop()

    async def cancel(self, interaction):
        await self.back(interaction, "Copy cancelled; no changes were written.")

    async def turn_page(self, interaction, *, targets=0, sources=0):
        self.page += targets
        self.source_page += sources
        self.build()
        await interaction.response.edit_message(view=self)

    async def previous(self, interaction):
        await self.turn_page(interaction, targets=-1)

    async def next(self, interaction):
        await self.turn_page(interaction, targets=1)

    async def previous_source(self, interaction):
        await self.turn_page(interaction, sources=-1)

    async def next_source(self, interaction):
        await self.turn_page(interaction, sources=1)
