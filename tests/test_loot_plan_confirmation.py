"""Focused Step 9 tests for atomic persisted-plan confirmation."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    BisSet,
    BisSetItem,
    Character,
    CharacterAugmentationInventory,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    CharacterKind,
    ClearMode,
    ConfirmedReclearMaterialGrant,
    DiscordGuild,
    GearClassification,
    GearSlot,
    Item,
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
    ReclearFloorCompletion,
    ReclearWeek,
    Static,
    StaticMember,
    WeeklyLockout,
    WeeklyLootPlanStatus,
)
from app.schemas.loot_plan_confirmation import (
    LootPlanIntegrityError,
    LootPlanNotReadyError,
    LootPlanStaleError,
)
from app.services import generate_and_persist_loot_plan
from app.services.loot_plan_confirmation import confirm_loot_plan
from tests.test_regular_loot_planning import RegularFixture
from tests.test_split_savage_planning import make_split_savage_fixture


def test_regular_persisted_plan_applies_exact_gear_materials_books_and_is_idempotent(session):
    guild = DiscordGuild(discord_guild_id=991901, name="Confirmation Guild")
    tier = RaidTier(code="CONFIRM", name="Confirmation Tier")
    static = Static(guild=guild, name="Confirmation Static", active_raid_tier=tier)
    floors = [RaidFloor(raid_tier=tier, floor_number=i, name=f"Floor {i}") for i in range(1, 5)]
    head = GearSlot(code="HEAD", display_name="Head", sort_order=1)
    item = Item(name="Exact Head")
    job = Job(abbreviation="SAM", name="Samurai")
    bis = BisSet(job=job, raid_tier=tier, name="Main BiS")
    requirement = BisSetItem(
        bis_set=bis,
        gear_slot=head,
        classification=GearClassification.SAVAGE,
        desired_item=item,
        raid_floor=floors[0],
    )
    members = [
        StaticMember(static=static, discord_user_id=991902 + i, display_name=f"Member {i}")
        for i in range(8)
    ]
    mains = [
        Character(
            static_member=member,
            job=job,
            name=f"Main {i}",
            world="Fictional",
            kind=CharacterKind.MAIN,
        )
        for i, member in enumerate(members)
    ]
    main = mains[0]
    session.add_all([static, tier, *floors, head, item, job, bis, requirement, *members, *mains])
    session.flush()
    selection = __import__("app.models", fromlist=["CharacterBisSelection"]).CharacterBisSelection(
        character=main, raid_tier=tier, bis_set=bis
    )
    week = ReclearWeek(
        static=static,
        raid_tier=tier,
        week_start=date(2026, 9, 1),
        clear_mode=ClearMode.REGULAR,
    )
    plan = LootPlan(
        reclear_week=week,
        name="Confirmation Plan",
        mode=ClearMode.REGULAR,
        status=WeeklyLootPlanStatus.READY,
        source_snapshot='{"scope":{"target_week":2}}',
        source_snapshot_version=1,
        source_state_hash="unused",
    )
    run = LootPlanRun(loot_plan=plan, run_number=1, name="Regular")
    run.participants = [
        LootPlanParticipant(character=character, designation=CharacterKind.MAIN)
        for character in mains
    ]
    weapon = LootType(
        raid_tier=tier, code="WEAPON_COFFER", name="Weapon", category=LootCategory.COFFER
    )
    LootAssignment(
        loot_plan=plan,
        plan_run=run,
        raid_floor=floors[0],
        loot_type=weapon,
        intended_character=main,
        intended_bis_set_item=requirement,
        intended_final_item=item,
        expected_drop_instance=1,
        disposition=PlannedLootDisposition.ASSIGNED,
        recipient_designation=CharacterKind.MAIN,
    )
    session.add_all([selection, plan, weapon])
    session.flush()
    # Make staleness validation authoritative for this fictional fixture.
    from app.services.loot_plan_source import build_source_snapshot

    snapshot, digest = build_source_snapshot(
        session,
        static.id,
        ClearMode.REGULAR,
        2,
        tier.id,
        tuple(character.id for character in mains),
    )
    plan.source_snapshot = snapshot
    plan.source_state_hash = digest
    session.commit()

    result = confirm_loot_plan(session, plan.id, 991999)
    session.commit()
    assert result.changes_applied is True
    assert result.resulting_status is WeeklyLootPlanStatus.APPLIED
    assert plan.status is WeeklyLootPlanStatus.APPLIED
    assert (
        session.scalar(
            select(CharacterGearSlot).where(CharacterGearSlot.character_id == main.id)
        ).current_classification
        is GearClassification.SAVAGE
    )
    assert session.scalar(select(func.count()).select_from(ReclearFloorCompletion)) == 4
    assert session.scalar(select(func.count()).select_from(WeeklyLockout)) == 32
    assert (
        session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "LOOT_PLAN_APPLIED")
        )
        == 1
    )

    second = confirm_loot_plan(session, plan.id, 991999)
    assert second.already_applied is True
    assert second.changes_applied is False


def _generated_regular(session):
    fixture = RegularFixture(session)
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 992000)
    session.commit()
    return fixture, result


def _counts(session):
    return {
        "gear": session.scalar(select(func.count()).select_from(CharacterGearSlot)),
        "materials": session.scalar(
            select(func.count()).select_from(CharacterAugmentationInventory)
        ),
        "grants": session.scalar(select(func.count()).select_from(ConfirmedReclearMaterialGrant)),
        "books": session.scalar(select(func.count()).select_from(CharacterFloorBookBalance)),
        "clears": session.scalar(select(func.count()).select_from(ReclearFloorCompletion)),
        "lockouts": session.scalar(select(func.count()).select_from(WeeklyLockout)),
        "audits": session.scalar(select(func.count()).select_from(AuditLog)),
    }


def test_generated_regular_plan_confirms_and_preserves_book_balances(session):
    fixture, result = _generated_regular(session)
    first = fixture.mains[0]
    before = CharacterFloorBookBalance(
        character=first,
        raid_floor=fixture.floors[1],
        earned=7,
        spent=3,
        manual_adjustment=2,
    )
    session.add(before)
    session.commit()

    confirmed = confirm_loot_plan(session, result.plan_id, 992001)
    session.commit()

    assert confirmed.changes_applied is True
    assert confirmed.book_increment_count == 32
    assert confirmed.clear_record_count == 4
    assert confirmed.savage_gear_update_count == 10
    assert session.get(LootPlan, result.plan_id).status is WeeklyLootPlanStatus.APPLIED
    balance = session.scalar(
        select(CharacterFloorBookBalance).where(
            CharacterFloorBookBalance.character_id == first.id,
            CharacterFloorBookBalance.raid_floor_id == fixture.floors[1].id,
        )
    )
    assert balance.earned == 8
    assert balance.spent == 3
    assert balance.manual_adjustment == 2
    assert (
        session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "LOOT_PLAN_APPLIED")
        )
        == 1
    )


def test_generated_regular_confirmation_is_idempotent_without_new_rows(session):
    _fixture, result = _generated_regular(session)
    first = confirm_loot_plan(session, result.plan_id, 992002)
    session.commit()
    counts = _counts(session)

    second = confirm_loot_plan(session, result.plan_id, 992003)

    assert second.already_applied is True
    assert second.changes_applied is False
    assert _counts(session) == counts
    assert first.applied_at.replace(tzinfo=None) == second.applied_at.replace(tzinfo=None)


@pytest.mark.parametrize("status", [WeeklyLootPlanStatus.DRAFT, WeeklyLootPlanStatus.CANCELLED])
def test_non_ready_plan_is_rejected_without_writes(session, status):
    _fixture, result = _generated_regular(session)
    plan = session.get(LootPlan, result.plan_id)
    plan.status = status
    session.commit()
    before = _counts(session)

    with pytest.raises(LootPlanNotReadyError):
        confirm_loot_plan(session, result.plan_id, 992004)

    assert _counts(session) == before


def test_stale_snapshot_is_rejected_without_writes(session):
    fixture, result = _generated_regular(session)
    fixture.mains[0].gear_slots.append(
        CharacterGearSlot(
            gear_slot=fixture.slots[next(code for code in fixture.slots if code.value == "HEAD")],
            current_classification=GearClassification.SAVAGE,
        )
    )
    session.commit()
    before = _counts(session)

    with pytest.raises(LootPlanStaleError):
        confirm_loot_plan(session, result.plan_id, 992005)

    assert _counts(session) == before
    assert session.get(LootPlan, result.plan_id).status is WeeklyLootPlanStatus.READY


def test_invalid_persisted_recipient_is_rejected_before_mutation(session):
    fixture, result = _generated_regular(session)
    plan = session.get(LootPlan, result.plan_id)
    assignment = next(row for row in plan.assignments if row.intended_character_id is not None)
    assignment.intended_character_id = fixture.alts[0].id
    session.commit()
    before = _counts(session)

    with pytest.raises(LootPlanIntegrityError):
        confirm_loot_plan(session, result.plan_id, 992006)

    assert _counts(session) == before
    assert plan.status is WeeklyLootPlanStatus.READY


def test_failure_after_gear_update_rolls_back_everything(session, monkeypatch):
    _fixture, result = _generated_regular(session)
    before = _counts(session)
    import app.services.loot_plan_confirmation as confirmation

    original = confirmation.set_gear

    def fail_after_update(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure after gear")

    monkeypatch.setattr(confirmation, "set_gear", fail_after_update)
    with pytest.raises(RuntimeError, match="injected failure"):
        confirm_loot_plan(session, result.plan_id, 992007)
    session.rollback()

    assert _counts(session) == before
    plan = session.get(LootPlan, result.plan_id)
    assert plan.status is WeeklyLootPlanStatus.READY
    assert plan.applied_at is None


def test_split_plan_rejects_main_alt_member_duplication_before_writes(session):
    fixture = make_split_savage_fixture(session)
    from app.models import FloorLootRule

    for number, code in ((2, "WEAPON_TOMESTONE"), (3, "WEAPON_AUGMENT")):
        loot_type = LootType(
            raid_tier=fixture.tier,
            code=code,
            name=code.replace("_", " ").title(),
            category=LootCategory.OTHER,
        )
        fixture.floors[number].loot_rules.append(
            FloorLootRule(loot_type=loot_type, expected_quantity=1)
        )
    session.commit()
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.SPLIT, 992008)
    session.commit()
    plan = session.get(LootPlan, result.plan_id)
    first_run, second_run = sorted(plan.runs, key=lambda row: row.run_number)
    second_run.participants[0].designation = first_run.participants[0].designation
    session.commit()
    before = _counts(session)

    with pytest.raises(LootPlanIntegrityError):
        confirm_loot_plan(session, result.plan_id, 992009)

    assert _counts(session) == before
