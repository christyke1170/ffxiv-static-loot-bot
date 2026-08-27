"""Discord-independent weekly setup, board, and persistent-control tests."""

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.database import create_session_factory
from app.models import (
    Character,
    CharacterKind,
    ClearMode,
    DiscordGuild,
    Job,
    JobHierarchy,
    JobHierarchyEntry,
    LootAssignmentState,
    RaidFloor,
    RaidTier,
    ReclearFloorCompletion,
    ReclearParticipant,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    StaticMember,
)
from app.services.loot_formatting import assignment_detail, loot_board_table
from app.services.reclear import (
    cancel_reclear_week,
    create_reclear_week,
    load_loot_board,
    mark_assignment_disposition,
    preview_rosters,
    reclear_status,
)
from bot.commands.loot import Loot
from bot.commands.reclear import Reclear
from bot.views.confirmation import (
    ConfirmationView,
    confirmation_custom_id,
    register_persistent_confirmation_views,
)
from bot.views.reclear import SetupPreviewView, roster_text
from tests.bot.fakes import FakeDiscordMember, FakeInteraction, FakeRole, registered_command
from tests.test_planning import PlanningFixture

TODAY = date(2026, 8, 25)


def setup_static(session, *, tier=True, hierarchy=True):
    guild = DiscordGuild(discord_guild_id=4444, name="Weekly Guild")
    raid_tier = RaidTier(code="WEEKLY", name="Weekly Tier")
    raid_tier.floors.append(RaidFloor(floor_number=1, name="M1S"))
    static = Static(
        guild=guild,
        name="Weekly Static",
        active_raid_tier=raid_tier if tier else None,
    )
    jobs = []
    for index in range(8):
        job = Job(abbreviation=f"W{index + 1}", name=f"Weekly Job {index + 1}")
        jobs.append(job)
        member = StaticMember(
            static=static,
            discord_user_id=5000 + index,
            display_name=f"Member {index + 1}",
        )
        member.characters.extend(
            [
                Character(
                    job=job,
                    name=f"Main {index + 1}",
                    world="Weekly",
                    kind=CharacterKind.MAIN,
                ),
                Character(
                    job=job,
                    name=f"Alt {index + 1}",
                    world="Weekly",
                    kind=CharacterKind.ALT,
                ),
            ]
        )
    if hierarchy:
        active = JobHierarchy(static=static, version=1, active_marker=True)
        active.entries = [
            JobHierarchyEntry(job=job, position=index) for index, job in enumerate(jobs, 1)
        ]
    session.add(static)
    session.commit()
    return static


def test_regular_setup_automatically_persists_eight_mains(session):
    static = setup_static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, today=TODAY)

    assert week.workflow_state.value == "DRAFT"
    assert len(week.groups) == 1
    assert len(week.groups[0].participants) == 8
    assert {row.character.kind for row in week.groups[0].participants} == {CharacterKind.MAIN}
    assert len(week.hierarchy_snapshot) == 8


def test_split_selector_requires_exactly_four(session):
    static = setup_static(session)
    with pytest.raises(ValueError, match="exactly four"):
        preview_rosters(session, static, ClearMode.SPLIT, {static.members[0].id})


def test_split_a_and_b_are_complementary(session):
    static = setup_static(session)
    chosen = {member.id for member in static.members[:4]}
    split_a, split_b = preview_rosters(session, static, ClearMode.SPLIT, chosen)

    assert [row.kind for row in split_a] == [CharacterKind.MAIN] * 4 + [CharacterKind.ALT] * 4
    assert [row.kind for row in split_b] == [CharacterKind.ALT] * 4 + [CharacterKind.MAIN] * 4
    assert {row.id for row in split_a}.isdisjoint({row.id for row in split_b})


def test_split_preview_does_not_persist(session):
    static = setup_static(session)
    chosen = {member.id for member in static.members[:4]}
    preview_rosters(session, static, ClearMode.SPLIT, chosen)

    assert session.scalar(select(func.count()).select_from(ReclearWeek)) == 0
    assert session.scalar(select(func.count()).select_from(ReclearParticipant)) == 0


def test_duplicate_weekly_setup_is_rejected(session):
    static = setup_static(session)
    create_reclear_week(session, static, ClearMode.REGULAR, today=TODAY)
    with pytest.raises(ValueError, match="already exists"):
        create_reclear_week(session, static, ClearMode.REGULAR, today=TODAY)


@pytest.mark.parametrize(
    ("tier", "hierarchy", "message"),
    [(False, True, "raid tier"), (True, False, "job hierarchy")],
)
def test_setup_rejects_missing_tier_or_hierarchy(session, tier, hierarchy, message):
    static = setup_static(session, tier=tier, hierarchy=hierarchy)
    with pytest.raises(ValueError, match=message):
        create_reclear_week(session, static, ClearMode.REGULAR, today=TODAY)


def test_status_reads_persisted_values(session):
    static = setup_static(session)
    week = create_reclear_week(
        session, static, ClearMode.REGULAR, today=TODAY, notes="database note"
    )
    week.workflow_state = ReclearWorkflowState.PLANNED
    session.commit()

    result = reclear_status(session, static.id, today=TODAY)

    assert result.week_id == week.id
    assert result.week_start == TODAY
    assert result.workflow_state.value == "PLANNED"
    assert result.tier_name == "Weekly Tier"
    assert len(result.groups[0].entries) == 8


def test_roster_preview_text_contains_both_split_rosters(session):
    static = setup_static(session)
    chosen = {member.id for member in static.members[:4]}
    text = roster_text(preview_rosters(session, static, ClearMode.SPLIT, chosen))
    assert "Split A" in text and "Split B" in text
    assert "Main 1 (Main)" in text and "Alt 1 (Alt)" in text
    assert len(text) <= 1900


def test_loot_board_formatting_and_assignment_details(session):
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    fixture.select_bis(fixture.mains[0])
    from app.services import generate_weekly_loot_plan

    generated = generate_weekly_loot_plan(session, fixture.week.id)
    board = load_loot_board(session, fixture.static.id, today=TODAY)
    table, page_count = loot_board_table(board)
    details = assignment_detail(board.rows[0])

    assert page_count == 1
    assert all(
        label in table for label in ("Floor", "Split", "Drop", "Recipient", "Backup", "Status")
    )
    assert "Fictional O" in table
    assert generated.assignments[0].loot_type.name in details
    assert "Confirmation history" in details and "Distribution errors" in details
    assert len(table) <= 2000 and len(details) <= 2000


@pytest.mark.parametrize(
    ("state", "label"),
    [(LootAssignmentState.LEFTOVER, "LEFTOVER"), (LootAssignmentState.FREE_ROLL, "FREE_ROLL")],
)
def test_leftover_and_free_roll_do_not_create_completion(session, state, label):
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    fixture.select_bis(fixture.mains[0])
    from app.services import generate_weekly_loot_plan

    assignment = generate_weekly_loot_plan(session, fixture.week.id).assignments[0].assignment
    mark_assignment_disposition(session, fixture.static.id, assignment.id, state, "reason", 99)

    assert assignment.state.value == label
    assert session.scalar(select(func.count()).select_from(ReclearFloorCompletion)) == 0


def test_cancel_requires_untouched_week_and_preserves_row(session):
    static = setup_static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, today=TODAY)
    cancelled = cancel_reclear_week(session, static.id, "schedule", 99, today=TODAY)
    assert cancelled.id == week.id and cancelled.workflow_state.value == "CANCELLED"
    assert session.get(ReclearWeek, week.id) is week


def test_persistent_custom_ids_have_only_identifiers_and_action():
    custom_id = confirmation_custom_id(12, 34, "yes")
    assert custom_id == "rc:12:34:yes"
    assert "Kek" not in custom_id and len(custom_id) < 100
    view = ConfirmationView(SimpleNamespace(), 12, 34)
    assert view.timeout is None
    assert all(
        "12:34" in item.custom_id
        for item in view.walk_children()
        if getattr(item, "custom_id", None)
    )


def test_split_setup_view_has_confirm_reselect_and_cancel(session):
    static = setup_static(session)
    view = SetupPreviewView(SimpleNamespace(), static.id, ClearMode.SPLIT, None, static.members)
    labels = {item.label for item in view.walk_children() if getattr(item, "label", None)}
    assert {"Confirm", "Reselect", "Cancel"} <= labels


def test_persistent_views_register_for_pending_assignments(session):
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    fixture.select_bis(fixture.mains[0])
    from app.services import generate_weekly_loot_plan, mark_reclear_floors_complete

    generate_weekly_loot_plan(session, fixture.week.id)
    mark_reclear_floors_complete(
        session, fixture.week.id, [(fixture.groups[0].id, fixture.floor.id)], 99
    )
    session.commit()
    views = []
    bot = SimpleNamespace(session_factory=lambda: session, add_view=views.append)

    count = register_persistent_confirmation_views(bot)

    assert count == 1 and len(views) == 1
    assert views[0].timeout is None


async def test_confirmation_permission_is_rechecked_on_callback(engine):
    bot = SimpleNamespace(
        settings=Settings(raid_leader_role_ids=(20,)),
        session_factory=create_session_factory(engine),
    )
    view = ConfirmationView(bot, 1, 1)
    interaction = FakeInteraction(bot, user=FakeDiscordMember(200, []))

    assert not await view.interaction_check(interaction)
    assert interaction.messages[0]["content"] == "Raid-leader permission is required."


async def test_stale_confirmation_is_rejected(engine):
    bot = SimpleNamespace(
        settings=Settings(raid_leader_role_ids=(20,)),
        session_factory=create_session_factory(engine),
    )
    view = ConfirmationView(bot, 999, 999)
    interaction = FakeInteraction(bot, user=FakeDiscordMember(200, [FakeRole(20)]))

    assert not await view.interaction_check(interaction)
    assert "Select a static first" in interaction.messages[0]["content"]


@pytest.mark.parametrize(
    ("cog", "commands"),
    [
        (Reclear, {"setup", "plan", "complete", "resume", "close", "cancel"}),
        (Loot, {"override", "leftover", "correction"}),
    ],
)
def test_all_weekly_write_commands_have_permission_checks(cog, commands):
    instance = cog(SimpleNamespace())
    assert all(registered_command(instance, name).checks for name in commands)
