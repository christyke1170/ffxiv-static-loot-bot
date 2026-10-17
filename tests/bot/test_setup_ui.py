"""Regression coverage for batch setup, scoped writes, and Discord payloads."""

import discord
import pytest
from sqlalchemy import func, select

from app.domain.jobs import JOB_CATALOG
from app.models import (
    BisSet,
    Character,
    CharacterGearSlot,
    CharacterKind,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Job,
    Static,
    StaticMember,
)
from app.services.seed import seed_reference_data
from bot.commands.bis import Bis
from bot.commands.character import Character as CharacterCog
from bot.services.admin import add_character
from bot.services.bis import save_bis
from bot.views.bis_setup import BisBatchView, BisCopyView, BisSetupView, saved_categories
from tests.bot.fakes import FakeDiscordMember, invoke_registered
from tests.bot.helpers import arrange_static


def arrange(bot, *, member=False):
    static_id = arrange_static(bot)
    with bot.session_factory() as session:
        seed_reference_data(session)
        if member:
            session.add(
                StaticMember(static_id=static_id, discord_user_id=200, display_name="Player")
            )
        session.commit()
    return static_id


def job_id(bot, abbreviation):
    with bot.session_factory() as session:
        return session.scalar(select(Job.id).where(Job.abbreviation == abbreviation))


def seed_bis(bot, static_id, abbreviation="SAM", category=GearClassification.SAVAGE):
    with bot.session_factory() as session:
        job = session.get(Job, job_id(bot, abbreviation))
        categories = {slot.code: category for slot in session.scalars(select(GearSlot))}
        if not job.uses_offhand:
            categories[GearSlotCode.OFFHAND] = GearClassification.NOT_APPLICABLE
        save_bis(session, session.get(Static, static_id), job, categories, 200)
        session.commit()


def assert_payload(view):
    assert isinstance(view, discord.ui.LayoutView)
    assert view.total_children_count <= 40
    assert view.to_components()
    for child in view.walk_children():
        if isinstance(child, discord.ui.Select):
            assert 1 <= len(child.options) <= 25
        if isinstance(child, discord.ui.ActionRow):
            assert sum(item.width for item in child.children) <= 5


async def test_setup_lists_every_job_without_roster_and_paginates_future_jobs(
    bot, interaction_factory
):
    static_id = arrange(bot)
    initial = interaction_factory()
    await invoke_registered(Bis(bot), "setup", initial)
    view = initial.followup.messages[0]["view"]
    assert len(view.picker.options) == len(JOB_CATALOG)
    assert "0/21 jobs" in view.to_components()[0]["content"]
    assert initial.followup.messages[0]["content"] is None
    assert_payload(view)
    with bot.session_factory() as session:
        for index in range(7):
            session.add(Job(abbreviation=f"F{index}", name=f"Future {index}", role="Tank"))
        session.commit()
    view = BisSetupView(bot, static_id, 200, 100)
    assert len(view.jobs) == 28
    assert len(view.picker.options) == 25
    changed = interaction_factory()
    await view.next(changed)
    assert len(changed.response.edits[0]["view"].picker.options) == 3
    assert_payload(changed.response.edits[0]["view"])


async def test_batch_edits_are_drafts_then_persist_and_read_back(bot, interaction_factory):
    static_id = arrange(bot)
    sam_id = job_id(bot, "SAM")
    view = BisBatchView(bot, static_id, sam_id, 200, 100)
    view.slot_picker._values = ["WEAPON", "BODY", "HANDS"]
    await view.choose_slots(interaction_factory())
    view.category_picker._values = ["SAVAGE"]
    await view.choose_category(interaction_factory())
    applied = interaction_factory()
    await view.apply(applied)
    assert_payload(view)
    with bot.session_factory() as session:
        assert saved_categories(session, static_id, sam_id) == {}
    await view.save(interaction_factory())
    reopened = BisBatchView(bot, static_id, sam_id, 200, 100)
    assert reopened.categories[GearSlotCode.BODY] is GearClassification.SAVAGE
    assert reopened.categories[GearSlotCode.LEGS] is GearClassification.CRAFTED_EX
    assert reopened.categories[GearSlotCode.OFFHAND] is GearClassification.NOT_APPLICABLE
    assert_payload(reopened)


async def test_batch_cancel_and_stale_save_do_not_overwrite(bot, interaction_factory):
    static_id = arrange(bot)
    sam_id = job_id(bot, "SAM")
    view = BisBatchView(bot, static_id, sam_id, 200, 100)
    view.categories[GearSlotCode.WEAPON] = GearClassification.SAVAGE
    await view.cancel(interaction_factory())
    with bot.session_factory() as session:
        assert saved_categories(session, static_id, sam_id) == {}
    view = BisBatchView(bot, static_id, sam_id, 200, 100)
    seed_bis(bot, static_id)
    stale = interaction_factory()
    await view.save(stale)
    assert "changed" in stale.messages[0]["content"]
    with bot.session_factory() as session:
        assert (
            saved_categories(session, static_id, sam_id)[GearSlotCode.WEAPON]
            is GearClassification.SAVAGE
        )


async def test_copy_requires_review_adapts_offhand_and_saves_multiple_jobs(
    bot, interaction_factory
):
    static_id = arrange(bot)
    seed_bis(bot, static_id)
    view = BisCopyView(bot, static_id, 200, 100)
    view.source_picker._values = [str(job_id(bot, "SAM"))]
    await view.choose_source(interaction_factory())
    view.target_picker._values = [str(job_id(bot, "PLD")), str(job_id(bot, "WAR"))]
    await view.choose_targets(interaction_factory())
    rejected = interaction_factory()
    await view.save(rejected)
    assert "Review" in rejected.messages[0]["content"]
    await view.preview(interaction_factory())
    assert_payload(view)
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(BisSet)) == 1
    saved = interaction_factory()
    await view.save(saved)
    assert "content" not in saved.response.edits[0]
    overview = saved.response.edits[0]["view"]
    assert len(overview.configured) == 3
    with bot.session_factory() as session:
        pld = saved_categories(session, static_id, job_id(bot, "PLD"))
        war = saved_categories(session, static_id, job_id(bot, "WAR"))
        assert pld[GearSlotCode.OFFHAND] is GearClassification.SAVAGE
        assert war[GearSlotCode.OFFHAND] is GearClassification.NOT_APPLICABLE


async def test_copy_rolls_back_all_targets_when_one_changed(bot, interaction_factory):
    static_id = arrange(bot)
    seed_bis(bot, static_id)
    view = BisCopyView(bot, static_id, 200, 100)
    view.source_id = job_id(bot, "SAM")
    view.targets = {job_id(bot, "PLD"), job_id(bot, "WAR")}
    await view.preview(interaction_factory())
    seed_bis(bot, static_id, "WAR", GearClassification.TOME)
    failed = interaction_factory()
    await view.save(failed)
    assert "changed" in failed.messages[0]["content"]
    with bot.session_factory() as session:
        assert saved_categories(session, static_id, job_id(bot, "PLD")) == {}
        assert (
            saved_categories(session, static_id, job_id(bot, "WAR"))[GearSlotCode.WEAPON]
            is GearClassification.TOME
        )


async def test_copy_can_configure_every_supported_job(bot, interaction_factory):
    static_id = arrange(bot)
    seed_bis(bot, static_id)
    view = BisCopyView(bot, static_id, 200, 100)
    view.source_id = job_id(bot, "SAM")
    view.targets = {job.id for job in view.jobs if job.id != view.source_id}
    await view.preview(interaction_factory())
    saved = interaction_factory()
    await view.save(saved)
    overview = saved.response.edits[0]["view"]
    assert len(overview.configured) == len(JOB_CATALOG)


async def test_copy_selections_survive_pages_and_changes_require_new_review(
    bot, interaction_factory
):
    static_id = arrange(bot)
    seed_bis(bot, static_id)
    with bot.session_factory() as session:
        for index in range(7):
            session.add(Job(abbreviation=f"Z{index}", name=f"Future {index}", role="Tank"))
        session.commit()
    view = BisCopyView(bot, static_id, 200, 100)
    view.source_id = job_id(bot, "SAM")
    first = job_id(bot, "PLD")
    view.target_picker._values = [str(first)]
    await view.choose_targets(interaction_factory())
    await view.next(interaction_factory())
    last = int(view.target_picker.options[0].value)
    view.target_picker._values = [str(last)]
    await view.choose_targets(interaction_factory())
    assert view.targets == {first, last}
    await view.preview(interaction_factory())
    assert_payload(view)
    await view.previous(interaction_factory())
    view.target_picker._values = []
    await view.choose_targets(interaction_factory())
    assert view.review is None
    assert view.targets == {last}
    rejected = interaction_factory()
    await view.save(rejected)
    assert "Review" in rejected.messages[0]["content"]


@pytest.mark.parametrize("kwargs", [{"roles": ()}, {"user_id": 999}, {"guild_id": 999}])
async def test_setup_rejects_wrong_role_owner_or_guild(bot, interaction_factory, kwargs):
    static_id = arrange(bot)
    view = BisBatchView(bot, static_id, job_id(bot, "SAM"), 200, 100)
    assert not await view.interaction_check(interaction_factory(**kwargs))


async def open_pair(bot, interaction_factory, *, job="SAM"):
    initial = interaction_factory(roles=())
    await invoke_registered(CharacterCog(bot), "setup", initial)
    view = initial.followup.messages[0]["view"]
    assert_payload(view)
    view.picker._values = [str(job_id(bot, job))]
    selected = interaction_factory(roles=())
    await view.choose_job(selected)
    modal = selected.response.modals[0]
    assert len(modal.children) == 4
    for field, value in zip(modal.fields, ("Main Hero", "World", "Alt Hero", "World"), strict=True):
        field._value = value
    submitted = interaction_factory(roles=())
    await modal.on_submit(submitted)
    assert "content" not in submitted.response.edits[0]
    assert_payload(view)
    return view


@pytest.mark.parametrize("job", ["SAM", "PLD"])
async def test_member_registers_pair_only_after_preview_and_preserves_crafted_defaults(
    bot, interaction_factory, job
):
    arrange(bot, member=True)
    view = await open_pair(bot, interaction_factory, job=job)
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Character)) == 0
    await view.save(interaction_factory(roles=()))
    with bot.session_factory() as session:
        rows = list(session.scalars(select(Character)))
        assert len(rows) == 2
        assert {row.kind for row in rows} == {CharacterKind.MAIN, CharacterKind.ALT}
        assert {row.job.abbreviation for row in rows} == {job}
        assert all(row.static_member.discord_user_id == 200 for row in rows)
        for row in rows:
            assert len(row.gear_slots) == 12
            assert next(
                g.current_classification
                for g in row.gear_slots
                if g.gear_slot.code is GearSlotCode.OFFHAND
            ) is (
                GearClassification.CRAFTED_EX if job == "PLD" else GearClassification.NOT_APPLICABLE
            )


async def test_pair_cancel_and_duplicate_alt_roll_back_both_characters(bot, interaction_factory):
    static_id = arrange(bot, member=True)
    view = await open_pair(bot, interaction_factory)
    await view.cancel(interaction_factory(roles=()))
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Character)) == 0
        owner = StaticMember(static_id=static_id, discord_user_id=999, display_name="Other")
        session.add(owner)
        session.flush()
        add_character(session, owner, "Alt Hero", "World", CharacterKind.ALT, "SAM")
        session.commit()
    view = await open_pair(bot, interaction_factory)
    failed = interaction_factory(roles=())
    await view.save(failed)
    assert "already exists" in failed.messages[0]["content"]
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Character)) == 1
        assert session.scalar(select(func.count()).select_from(CharacterGearSlot)) == 12


async def test_pair_reopen_retains_existing_gear_and_does_not_duplicate(bot, interaction_factory):
    arrange(bot, member=True)
    view = await open_pair(bot, interaction_factory)
    await view.save(interaction_factory(roles=()))
    with bot.session_factory() as session:
        gear = session.scalar(
            select(CharacterGearSlot).join(GearSlot).where(GearSlot.code == GearSlotCode.WEAPON)
        )
        gear.current_classification = GearClassification.SAVAGE
        session.commit()
    view = await open_pair(bot, interaction_factory)
    await view.save(interaction_factory(roles=()))
    with bot.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Character)) == 2
        assert (
            session.scalar(
                select(CharacterGearSlot).join(GearSlot).where(GearSlot.code == GearSlotCode.WEAPON)
            ).current_classification
            is GearClassification.SAVAGE
        )


async def test_member_cannot_register_for_another_member_or_submit_stale_modal(
    bot, interaction_factory
):
    static_id = arrange(bot, member=True)
    denied = interaction_factory(roles=())
    await invoke_registered(CharacterCog(bot), "setup", denied, FakeDiscordMember(id=999))
    assert "permission" in denied.messages[0]["content"]
    view = await open_pair(bot, interaction_factory)
    assert not await view.interaction_check(interaction_factory(user_id=999))
    with bot.session_factory() as session:
        session.get(Static, static_id).active = False
        session.commit()
    failed = interaction_factory(roles=())
    await view.save(failed)
    assert "stale" in failed.messages[0]["content"]
