"""Focused persistence tests for future weekly loot planning workflows."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import (
    AugmentationMaterialType,
    Character,
    CharacterKind,
    ClearMode,
    ConfirmedReclearMaterialGrant,
    DiscordGuild,
    Job,
    LootAssignment,
    LootCategory,
    LootPlan,
    LootPlanParticipant,
    LootPlanRun,
    LootType,
    PlannedLootDisposition,
    RaidFloor,
    RaidTier,
    ReclearWeek,
    Static,
    StaticMember,
    WeeklyLootPlanStatus,
)


@pytest.fixture
def planning_foundation(session):
    guild = DiscordGuild(discord_guild_id=991001, name="Foundation Guild")
    static = Static(guild=guild, name="Foundation Static")
    tier = RaidTier(code="FOUNDATION", name="Foundation Tier")
    floor = RaidFloor(raid_tier=tier, floor_number=1, name="Foundation One")
    weapon = LootType(
        raid_tier=tier,
        code="WEAPON_COFFER",
        name="Weapon Coffer",
        category=LootCategory.COFFER,
    )
    free_roll = LootType(
        raid_tier=tier,
        code="MOUNT",
        name="Mount",
        category=LootCategory.MOUNT,
    )
    twine = AugmentationMaterialType(raid_tier=tier, code="TWINE", name="Twine")
    glaze = AugmentationMaterialType(raid_tier=tier, code="GLAZE", name="Glaze")
    member = StaticMember(static=static, discord_user_id=991100, display_name="Planner")
    main = Character(
        static_member=member,
        job=Job(abbreviation="FMA", name="Foundation Main"),
        name="Foundation Main",
        world="Fictional",
        kind=CharacterKind.MAIN,
    )
    alt = Character(
        static_member=member,
        job=Job(abbreviation="FAL", name="Foundation Alt"),
        name="Foundation Alt",
        world="Fictional",
        kind=CharacterKind.ALT,
    )
    session.add_all([static, floor, weapon, free_roll, twine, glaze, main, alt])
    session.flush()
    return static, tier, floor, weapon, free_roll, twine, glaze, main, alt


def make_plan(static, tier, mode, week_start, name):
    week = ReclearWeek(
        static=static,
        raid_tier=tier,
        week_start=week_start,
        clear_mode=mode,
    )
    return LootPlan(
        reclear_week=week,
        name=name,
        mode=mode,
        status=WeeklyLootPlanStatus.DRAFT,
        created_by_discord_user_id=991100,
    )


def test_regular_and_split_plans_persist_runs_participants_and_designations(
    session, planning_foundation
):
    static, tier, *_, main, alt = planning_foundation
    regular = make_plan(static, tier, ClearMode.REGULAR, date(2026, 9, 1), "Regular")
    regular_run = LootPlanRun(loot_plan=regular, run_number=1, name="Regular")
    regular_run.participants.append(
        LootPlanParticipant(character=main, designation=CharacterKind.MAIN)
    )
    split = make_plan(static, tier, ClearMode.SPLIT, date(2026, 9, 8), "Split")
    run_a = LootPlanRun(loot_plan=split, run_number=1, name="Split Run A")
    run_b = LootPlanRun(loot_plan=split, run_number=2, name="Split Run B")
    run_a.participants.append(LootPlanParticipant(character=main, designation=CharacterKind.MAIN))
    run_b.participants.append(LootPlanParticipant(character=alt, designation=CharacterKind.ALT))
    session.add_all([regular, split])
    session.commit()
    session.expire_all()

    plans = session.scalars(select(LootPlan).order_by(LootPlan.id)).all()
    assert plans[0].mode is ClearMode.REGULAR
    assert [run.name for run in plans[0].runs] == ["Regular"]
    assert plans[1].mode is ClearMode.SPLIT
    assert [run.name for run in plans[1].runs] == ["Split Run A", "Split Run B"]
    assert plans[1].runs[0].participants[0].designation is CharacterKind.MAIN
    assert plans[1].runs[1].participants[0].designation is CharacterKind.ALT
    assert plans[0].created_at is not None and plans[0].updated_at is not None


def test_assigned_free_roll_and_paired_alt_weapon_assignments(session, planning_foundation):
    static, tier, floor, weapon, free_roll, *_, alt = planning_foundation
    plan = make_plan(static, tier, ClearMode.SPLIT, date(2026, 9, 1), "Assignments")
    run = LootPlanRun(loot_plan=plan, run_number=1, name="Split Run A")
    first = LootAssignment(
        loot_plan=plan,
        plan_run=run,
        raid_floor=floor,
        loot_type=weapon,
        expected_drop_instance=1,
        intended_character=alt,
        recipient_designation=CharacterKind.ALT,
        disposition=PlannedLootDisposition.ASSIGNED,
    )
    second = LootAssignment(
        loot_plan=plan,
        plan_run=run,
        raid_floor=floor,
        loot_type=weapon,
        expected_drop_instance=2,
        intended_character=alt,
        recipient_designation=CharacterKind.ALT,
        disposition=PlannedLootDisposition.ASSIGNED,
        paired_assignment=first,
    )
    unclaimed = LootAssignment(
        loot_plan=plan,
        plan_run=run,
        raid_floor=floor,
        loot_type=free_roll,
        disposition=PlannedLootDisposition.FREE_ROLL,
    )
    session.add_all([first, second, unclaimed])
    session.commit()

    assert first.intended_character is alt and first.recipient_designation is CharacterKind.ALT
    assert second.paired_assignment is first
    assert unclaimed.intended_character is None
    assert unclaimed.disposition is PlannedLootDisposition.FREE_ROLL


def test_twine_and_glaze_grants_are_separate_confirmed_reclear_history(
    session, planning_foundation
):
    static, tier, floor, weapon, _, twine, glaze, main, _ = planning_foundation
    plan = make_plan(static, tier, ClearMode.REGULAR, date(2026, 9, 1), "Materials")
    run = LootPlanRun(loot_plan=plan, run_number=1, name="Regular")
    twine_assignment = LootAssignment(
        loot_plan=plan,
        plan_run=run,
        raid_floor=floor,
        loot_type=weapon,
        expected_drop_instance=1,
        intended_character=main,
        recipient_designation=CharacterKind.MAIN,
        disposition=PlannedLootDisposition.ASSIGNED,
    )
    glaze_assignment = LootAssignment(
        loot_plan=plan,
        plan_run=run,
        raid_floor=floor,
        loot_type=weapon,
        expected_drop_instance=2,
        intended_character=main,
        recipient_designation=CharacterKind.MAIN,
        disposition=PlannedLootDisposition.ASSIGNED,
    )
    twine_assignment.material_grant = ConfirmedReclearMaterialGrant(
        character=main,
        augmentation_material_type=twine,
        confirmed_by_discord_user_id=991100,
    )
    glaze_assignment.material_grant = ConfirmedReclearMaterialGrant(
        character=main,
        augmentation_material_type=glaze,
        confirmed_by_discord_user_id=991100,
    )
    session.add(plan)
    session.commit()

    history = session.scalars(
        select(ConfirmedReclearMaterialGrant).order_by(
            ConfirmedReclearMaterialGrant.augmentation_material_type_id
        )
    ).all()
    assert {grant.augmentation_material_type.code for grant in history} == {"TWINE", "GLAZE"}
    assert all(grant.confirmed_at is not None for grant in history)
    assert len(history) == 2


def test_duplicate_character_in_run_is_rejected(session, planning_foundation):
    static, tier, *_, main, _ = planning_foundation
    plan = make_plan(static, tier, ClearMode.REGULAR, date(2026, 9, 1), "Duplicate Roster")
    run = LootPlanRun(loot_plan=plan, run_number=1, name="Regular")
    run.participants.extend(
        [
            LootPlanParticipant(character=main, designation=CharacterKind.MAIN),
            LootPlanParticipant(character=main, designation=CharacterKind.MAIN),
        ]
    )
    session.add(plan)
    with pytest.raises(IntegrityError):
        session.commit()


def test_duplicate_assignment_and_material_confirmation_are_rejected(session, planning_foundation):
    static, tier, floor, weapon, _, twine, _, main, _ = planning_foundation
    plan = make_plan(static, tier, ClearMode.REGULAR, date(2026, 9, 1), "Duplicate Loot")
    run = LootPlanRun(loot_plan=plan, run_number=1, name="Regular")
    assignment = LootAssignment(
        loot_plan=plan,
        plan_run=run,
        raid_floor=floor,
        loot_type=weapon,
        expected_drop_instance=1,
        intended_character=main,
        recipient_designation=CharacterKind.MAIN,
        disposition=PlannedLootDisposition.ASSIGNED,
    )
    session.add(assignment)
    session.commit()

    session.add(
        LootAssignment(
            loot_plan=plan,
            plan_run=run,
            raid_floor=floor,
            loot_type=weapon,
            expected_drop_instance=1,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add_all(
        [
            ConfirmedReclearMaterialGrant(
                loot_assignment_id=assignment.id,
                character=main,
                augmentation_material_type=twine,
                confirmed_by_discord_user_id=991100,
            ),
            ConfirmedReclearMaterialGrant(
                loot_assignment_id=assignment.id,
                character=main,
                augmentation_material_type=twine,
                confirmed_by_discord_user_id=991100,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_plan_lifecycle_and_relationship_cascades(session, planning_foundation):
    static, tier, floor, weapon, _, twine, _, main, _ = planning_foundation
    plan = make_plan(static, tier, ClearMode.REGULAR, date(2026, 9, 1), "Applied History")
    plan.status = WeeklyLootPlanStatus.APPLIED
    plan.applied_at = datetime.now(UTC)
    run = LootPlanRun(loot_plan=plan, run_number=1, name="Regular")
    run.participants.append(LootPlanParticipant(character=main, designation=CharacterKind.MAIN))
    assignment = LootAssignment(
        loot_plan=plan,
        plan_run=run,
        raid_floor=floor,
        loot_type=weapon,
        intended_character=main,
        recipient_designation=CharacterKind.MAIN,
        disposition=PlannedLootDisposition.ASSIGNED,
    )
    assignment.material_grant = ConfirmedReclearMaterialGrant(
        character=main,
        augmentation_material_type=twine,
        confirmed_by_discord_user_id=991100,
    )
    session.add(plan)
    session.commit()
    assert plan.status is WeeklyLootPlanStatus.APPLIED and plan.applied_at is not None

    session.delete(plan)
    session.commit()
    assert session.scalar(select(func.count()).select_from(LootPlanRun)) == 0
    assert session.scalar(select(func.count()).select_from(LootPlanParticipant)) == 0
    assert session.scalar(select(func.count()).select_from(LootAssignment)) == 0
    assert session.scalar(select(func.count()).select_from(ConfirmedReclearMaterialGrant)) == 0
