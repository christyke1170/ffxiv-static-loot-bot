"""BiS selection, weekly reclear, hierarchy, and confirmation tests."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AugmentationMaterialType,
    BisSet,
    BisSetItem,
    Character,
    CharacterBisSelection,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    CharacterKind,
    ClearMode,
    DiscordGuild,
    DistributionError,
    DistributionErrorType,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Item,
    Job,
    JobHierarchy,
    JobHierarchyEntry,
    LootAssignment,
    LootCategory,
    LootConfirmation,
    LootConfirmationType,
    LootPlan,
    LootType,
    RaidFloor,
    RaidTier,
    ReclearGroup,
    ReclearParticipant,
    ReclearWeek,
    Static,
    StaticMember,
)
from app.services.weeks import ResetPeriodPolicy, snapshot_hierarchy


def foundation(session: Session) -> tuple[Character, RaidTier, Static]:
    guild = DiscordGuild(discord_guild_id=9001, name="Workflow Guild")
    static = Static(guild=guild, name="Workflow Static")
    member = StaticMember(static=static, discord_user_id=8001, display_name="Leader")
    job = Job(abbreviation="RDM", name="Red Mage", role="Magical Ranged DPS")
    character = Character(
        static_member=member,
        job=job,
        name="Workflow Character",
        world="Test World",
        kind=CharacterKind.MAIN,
    )
    tier = RaidTier(code="WORKFLOW", name="Workflow Tier")
    session.add_all([character, tier])
    session.flush()
    return character, tier, static


def test_one_selected_bis_set_per_character_and_tier(session: Session) -> None:
    character, tier, _ = foundation(session)
    first = BisSet(job=character.job, raid_tier=tier, name="First")
    second = BisSet(job=character.job, raid_tier=tier, name="Second")
    session.add_all(
        [
            CharacterBisSelection(character=character, raid_tier=tier, bis_set=first),
            CharacterBisSelection(character=character, raid_tier=tier, bis_set=second),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_selection_from_wrong_tier_is_rejected(session: Session) -> None:
    character, tier, _ = foundation(session)
    other = RaidTier(code="OTHER", name="Other Tier")
    bis_set = BisSet(job=character.job, raid_tier=other, name="Other Set")
    session.add(CharacterBisSelection(character=character, raid_tier=tier, bis_set=bis_set))
    with pytest.raises(ValueError, match="another raid tier"):
        session.flush()


def test_augmented_tome_and_savage_requirements(session: Session) -> None:
    character, tier, _ = foundation(session)
    floor = RaidFloor(raid_tier=tier, floor_number=2, name="Second")
    coffer = LootType(raid_tier=tier, code="HEAD", name="Head Coffer", category=LootCategory.COFFER)
    material = AugmentationMaterialType(raid_tier=tier, code="POLISH", name="Polish")
    slot = GearSlot(code=GearSlotCode.HEAD, display_name="Head", sort_order=3)
    bis_set = BisSet(job=character.job, raid_tier=tier, name="Requirements")
    augmented = BisSetItem(
        bis_set=bis_set,
        gear_slot=slot,
        classification=GearClassification.AUGMENTED_TOME,
        desired_item=Item(name="Augmented Hat"),
        base_tome_item=Item(name="Tome Hat"),
        tome_cost=495,
        augmentation_material_type=material,
    )
    savage_slot = GearSlot(code=GearSlotCode.BODY, display_name="Body", sort_order=4)
    savage = BisSetItem(
        bis_set=bis_set,
        gear_slot=savage_slot,
        classification=GearClassification.SAVAGE,
        desired_item=Item(name="Savage Body"),
        raid_floor=floor,
        loot_type=coffer,
        book_cost=6,
    )
    session.add_all([augmented, savage])
    session.commit()
    assert augmented.base_tome_item.name == "Tome Hat"
    assert augmented.augmentation_material_type is material
    assert savage.raid_floor is floor and savage.loot_type is coffer


def test_invalid_augmented_tome_and_not_applicable(session: Session) -> None:
    character, tier, _ = foundation(session)
    slot = GearSlot(code=GearSlotCode.BODY, display_name="Body", sort_order=4)
    bis_set = BisSet(job=character.job, raid_tier=tier, name="Invalid")
    session.add(
        BisSetItem(
            bis_set=bis_set,
            gear_slot=slot,
            classification=GearClassification.AUGMENTED_TOME,
            desired_item=Item(name="Augmented Shield"),
        )
    )
    with pytest.raises(ValueError, match="base_tome_item"):
        session.flush()
    session.rollback()

    character, tier, _ = foundation(session)
    slot = GearSlot(code=GearSlotCode.OFFHAND, display_name="Offhand", sort_order=2)
    bis_set = BisSet(job=character.job, raid_tier=tier, name="N/A")
    valid = BisSetItem(
        bis_set=bis_set, gear_slot=slot, classification=GearClassification.NOT_APPLICABLE
    )
    session.add(valid)
    session.commit()
    assert valid.desired_item is None


def test_pld_not_applicable_offhand_is_rejected_by_model_validation(session: Session) -> None:
    character, tier, _ = foundation(session)
    character.job.abbreviation = "PLD"
    character.job.name = "Paladin"
    slot = GearSlot(code=GearSlotCode.OFFHAND, display_name="Offhand", sort_order=2)
    bis_set = BisSet(job=character.job, raid_tier=tier, name="Invalid PLD Offhand")
    session.add(
        BisSetItem(
            bis_set=bis_set,
            gear_slot=slot,
            classification=GearClassification.NOT_APPLICABLE,
        )
    )

    with pytest.raises(ValueError, match="PLD OFFHAND must define an applicable"):
        session.flush()


def test_effective_book_balance(session: Session) -> None:
    character, tier, _ = foundation(session)
    floor = RaidFloor(raid_tier=tier, floor_number=1, name="First")
    balance = CharacterFloorBookBalance(
        character=character, raid_floor=floor, earned=8, spent=5, manual_adjustment=-1
    )
    session.add(balance)
    session.commit()
    assert balance.available == 2


@pytest.mark.parametrize("mode, group_count", [(ClearMode.REGULAR, 1), (ClearMode.SPLIT, 2)])
def test_regular_versus_split_reclear_modes(
    session: Session, mode: ClearMode, group_count: int
) -> None:
    _, tier, static = foundation(session)
    week = ReclearWeek(static=static, raid_tier=tier, week_start=date(2026, 8, 25), clear_mode=mode)
    week.groups.extend(ReclearGroup(group_number=index) for index in range(1, group_count + 1))
    session.add(week)
    session.commit()
    assert week.clear_mode is mode
    assert len(week.groups) == group_count


def test_tuesday_reset_period_calculation() -> None:
    policy = ResetPeriodPolicy()
    assert policy.week_start(date(2026, 8, 25)) == date(2026, 8, 25)
    assert policy.week_start(date(2026, 8, 31)) == date(2026, 8, 25)
    assert policy.week_start(date(2026, 8, 24)) == date(2026, 8, 18)
    assert ResetPeriodPolicy(reset_weekday=0).week_start(date(2026, 8, 25)) == date(2026, 8, 24)


def test_one_weekly_reclear_per_static_and_period(session: Session) -> None:
    _, tier, static = foundation(session)
    session.add_all(
        [
            ReclearWeek(
                static=static,
                raid_tier=tier,
                week_start=date(2026, 8, 25),
                clear_mode=ClearMode.REGULAR,
            ),
            ReclearWeek(
                static=static,
                raid_tier=tier,
                week_start=date(2026, 8, 25),
                clear_mode=ClearMode.SPLIT,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_versioned_hierarchy_and_immutable_snapshot(session: Session) -> None:
    character, tier, static = foundation(session)
    second_job = Job(abbreviation="PCT", name="Pictomancer", role="Magical Ranged DPS")
    first = JobHierarchy(static=static, version=1, active_marker=True)
    first.entries.extend(
        [
            JobHierarchyEntry(job=character.job, position=1),
            JobHierarchyEntry(job=second_job, position=2),
        ]
    )
    session.add(first)
    session.flush()
    week = ReclearWeek(
        static=static, raid_tier=tier, week_start=date(2026, 8, 25), clear_mode=ClearMode.REGULAR
    )
    snapshot_hierarchy(week, first)
    first.active_marker = None
    second = JobHierarchy(static=static, version=2, active_marker=True)
    second.entries.extend(
        [
            JobHierarchyEntry(job=second_job, position=1),
            JobHierarchyEntry(job=character.job, position=2),
        ]
    )
    session.add_all([week, second])
    session.commit()
    assert not first.active and second.active
    assert [entry.job_abbreviation for entry in week.hierarchy_snapshot] == ["RDM", "PCT"]
    assert [entry.job.abbreviation for entry in second.entries] == ["PCT", "RDM"]


@pytest.mark.parametrize("duplicate", ["job", "position"])
def test_no_duplicate_hierarchy_jobs_or_positions(session: Session, duplicate: str) -> None:
    character, _, static = foundation(session)
    other = Job(abbreviation="PCT", name="Pictomancer", role="Magical Ranged DPS")
    hierarchy = JobHierarchy(static=static, version=1, active_marker=True)
    hierarchy.entries.extend(
        [
            JobHierarchyEntry(job=character.job, position=1),
            JobHierarchyEntry(
                job=character.job if duplicate == "job" else other,
                position=2 if duplicate == "job" else 1,
            ),
        ]
    )
    session.add(hierarchy)
    with pytest.raises(IntegrityError):
        session.commit()


def assignment_foundation(
    session: Session,
) -> tuple[LootAssignment, CharacterGearSlot, ReclearWeek]:
    character, tier, static = foundation(session)
    floor = RaidFloor(raid_tier=tier, floor_number=1, name="First")
    loot_type = LootType(raid_tier=tier, code="HEAD", name="Head", category=LootCategory.COFFER)
    slot = GearSlot(code=GearSlotCode.HEAD, display_name="Head", sort_order=3)
    current = CharacterGearSlot(
        character=character,
        gear_slot=slot,
        current_classification=GearClassification.GARBAGE,
    )
    week = ReclearWeek(
        static=static, raid_tier=tier, week_start=date(2026, 8, 25), clear_mode=ClearMode.REGULAR
    )
    plan = LootPlan(reclear_week=week, name="Plan")
    assignment = LootAssignment(
        loot_plan=plan,
        raid_floor=floor,
        loot_type=loot_type,
        intended_character=character,
        intended_final_item=Item(name="Intended Hat"),
    )
    session.add_all([assignment, current])
    session.flush()
    return assignment, current, week


def test_confirmation_history_distribution_error_and_failed_gear(session: Session) -> None:
    assignment, current, week = assignment_foundation(session)
    assignment.confirmations.extend(
        [
            LootConfirmation(
                confirmation_type=LootConfirmationType.RECEIVED,
                result=True,
                answered_by_discord_user_id=1,
            ),
            LootConfirmation(
                confirmation_type=LootConfirmationType.REDEEMED_CORRECTLY,
                result=False,
                answered_by_discord_user_id=1,
                note="Wrong coffer item selected",
            ),
        ]
    )
    incident = DistributionError(
        reclear_week=week,
        loot_assignment=assignment,
        intended_recipient=assignment.intended_character,
        error_type=DistributionErrorType.WRONG_COFFER_REDEMPTION,
        description="Coffer was redeemed for another slot.",
        reported_by_discord_user_id=1,
    )
    session.add(incident)
    session.commit()
    loaded = session.scalars(
        select(LootConfirmation).where(LootConfirmation.loot_assignment_id == assignment.id)
    ).all()
    assert [record.confirmation_type for record in loaded] == [
        LootConfirmationType.RECEIVED,
        LootConfirmationType.REDEEMED_CORRECTLY,
    ]
    assert (
        not loaded[1].result
        and incident.error_type is DistributionErrorType.WRONG_COFFER_REDEMPTION
    )
    assert current.current_classification is GearClassification.GARBAGE


def test_duplicate_character_in_different_groups_is_database_rejected(session: Session) -> None:
    character, tier, static = foundation(session)
    week = ReclearWeek(
        static=static, raid_tier=tier, week_start=date(2026, 8, 25), clear_mode=ClearMode.SPLIT
    )
    first = ReclearGroup(reclear_week=week, group_number=1)
    second = ReclearGroup(reclear_week=week, group_number=2)
    session.add_all([first, second])
    session.flush()
    session.add_all(
        [
            ReclearParticipant(reclear_week=week, group=first, character=character),
            ReclearParticipant(reclear_week=week, group=second, character=character),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()
