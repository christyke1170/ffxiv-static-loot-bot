"""Transactional reclear completion and confirmation service tests."""

import pytest
from sqlalchemy import func, select

from app.models import (
    AugmentationMaterialType,
    BisSet,
    BisSetItem,
    CharacterAugmentationInventory,
    CharacterBisSelection,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    ClearMode,
    ConfirmationQuestion,
    DistributionError,
    GearClassification,
    GearSlotCode,
    InventoryItem,
    Item,
    Job,
    LootAssignmentState,
    LootCategory,
    LootConfirmation,
    LootType,
    ReclearFloorCompletion,
    ReclearGroup,
    ReclearWorkflowState,
    WeeklyLockout,
)
from app.schemas.confirmations import ConfirmationError
from app.services import (
    close_reclear_week,
    confirm_augmentation_applied,
    confirm_coffer_redemption,
    confirm_loot_received,
    confirmation_progress,
    confirmation_queue,
    correct_confirmation,
    generate_weekly_loot_plan,
    mark_reclear_floors_complete,
)
from tests.test_planning import PlanningFixture
from tests.test_workflow import assignment_foundation


def test_regular_completion_awards_books_and_lockouts(session) -> None:
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    session.flush()

    mark_reclear_floors_complete(
        session,
        fixture.week.id,
        [(fixture.groups[0].id, fixture.floor.id)],
        99,
    )

    assert session.scalar(select(func.count()).select_from(ReclearFloorCompletion)) == 1
    assert session.scalar(select(func.count()).select_from(CharacterFloorBookBalance)) == 8
    assert session.scalar(select(func.count()).select_from(WeeklyLockout)) == 8
    assert fixture.week.workflow_state is ReclearWorkflowState.AWAITING_CONFIRMATION


def test_duplicate_completion_is_idempotent(session) -> None:
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    session.flush()
    request = [(fixture.groups[0].id, fixture.floor.id)]

    mark_reclear_floors_complete(session, fixture.week.id, request, 99)
    mark_reclear_floors_complete(session, fixture.week.id, request, 99)

    balances = session.scalars(select(CharacterFloorBookBalance)).all()
    assert len(balances) == 8
    assert all(balance.earned == 1 for balance in balances)


def test_split_completion_is_group_isolated_and_retry_safe(session) -> None:
    fixture = PlanningFixture(session, ClearMode.SPLIT)
    first_group = fixture.groups[0]
    second_group = fixture.groups[1]

    mark_reclear_floors_complete(session, fixture.week.id, [(first_group.id, fixture.floor.id)], 99)

    first_ids = {participant.character_id for participant in first_group.participants}
    second_ids = {participant.character_id for participant in second_group.participants}
    first_books = session.scalars(select(CharacterFloorBookBalance)).all()
    first_lockouts = session.scalars(select(WeeklyLockout)).all()
    assert {row.character_id for row in first_books} == first_ids
    assert {row.character_id for row in first_lockouts} == first_ids
    assert first_ids.isdisjoint(second_ids)

    mark_reclear_floors_complete(session, fixture.week.id, [(first_group.id, fixture.floor.id)], 99)
    assert all(row.earned == 1 for row in session.scalars(select(CharacterFloorBookBalance)))

    mark_reclear_floors_complete(
        session, fixture.week.id, [(second_group.id, fixture.floor.id)], 99
    )
    assert len(session.scalars(select(CharacterFloorBookBalance)).all()) == 16
    assert len(session.scalars(select(WeeklyLockout)).all()) == 16


def test_invalid_completion_is_atomic(session) -> None:
    fixture = PlanningFixture(session, ClearMode.SPLIT)
    with pytest.raises(ConfirmationError, match="does not belong"):
        mark_reclear_floors_complete(session, fixture.week.id, [(fixture.groups[0].id, 999999)], 99)

    assert session.scalar(select(func.count()).select_from(ReclearFloorCompletion)) == 0
    assert session.scalar(select(func.count()).select_from(CharacterFloorBookBalance)) == 0
    assert session.scalar(select(func.count()).select_from(WeeklyLockout)) == 0
    assert fixture.week.workflow_state is ReclearWorkflowState.DRAFT


def test_completion_failure_rolls_back_all_effects(session, monkeypatch) -> None:
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    calls = 0

    def fail_on_second_book(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected completion failure")
        from app.services import confirmations

        return confirmations._book_original(*args)

    from app.services import confirmations

    confirmations._book_original = confirmations._book
    monkeypatch.setattr(confirmations, "_book", fail_on_second_book)
    with pytest.raises(RuntimeError, match="injected"):
        mark_reclear_floors_complete(
            session, fixture.week.id, [(fixture.groups[0].id, fixture.floor.id)], 99
        )

    assert session.scalar(select(func.count()).select_from(ReclearFloorCompletion)) == 0
    assert session.scalar(select(func.count()).select_from(CharacterFloorBookBalance)) == 0
    assert session.scalar(select(func.count()).select_from(WeeklyLockout)) == 0
    assert fixture.week.workflow_state is ReclearWorkflowState.DRAFT


def test_queue_questions_follow_completed_floors_and_dependencies(session) -> None:
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    fixture.select_bis(fixture.mains[0])
    result = __import__(
        "app.services", fromlist=["generate_weekly_loot_plan"]
    ).generate_weekly_loot_plan(session, fixture.week.id)
    mark_reclear_floors_complete(
        session, fixture.week.id, [(fixture.groups[0].id, fixture.floor.id)], 99
    )
    queue = confirmation_queue(session, fixture.week.id)
    assert queue and queue[0].question is ConfirmationQuestion.RECEIVED
    assert queue[0].assignment.id == result.assignments[0].assignment.id

    from app.services import confirm_loot_received

    confirm_loot_received(session, queue[0].assignment.id, True, 99)
    assert (
        confirmation_queue(session, fixture.week.id)[0].question
        is ConfirmationQuestion.REDEEMED_CORRECTLY
    )


def _coffer_assignment(session):
    assignment, current, week = assignment_foundation(session)
    assignment.loot_type.item = Item(name="Head Coffer Item")
    bis_set = BisSet(
        job=assignment.intended_character.job, raid_tier=week.raid_tier, name="Confirm"
    )
    requirement = BisSetItem(
        bis_set=bis_set,
        gear_slot=current.gear_slot,
        classification="SAVAGE",
        raid_floor=assignment.raid_floor,
        loot_type=assignment.loot_type,
    )
    assignment.intended_bis_set_item = requirement
    session.commit()
    return assignment, current, week


def test_coffer_receipt_redemption_and_correction(session) -> None:
    assignment, current, week = _coffer_assignment(session)
    confirm_loot_received(session, assignment.id, True, 10)
    inventory = session.scalar(
        select(InventoryItem).where(
            InventoryItem.character_id == assignment.intended_character_id,
            InventoryItem.loot_type_id == assignment.loot_type_id,
        )
    )
    assert inventory.quantity == 1
    confirm_coffer_redemption(session, assignment.id, True, 10)
    assert inventory.quantity == 0
    assert current.current_classification is GearClassification.SAVAGE
    correct_confirmation(session, assignment.id, ConfirmationQuestion.REDEEMED_CORRECTLY, False, 11)
    assert current.current_classification is GearClassification.GARBAGE
    assert len(session.scalars(select(LootConfirmation)).all()) == 3


def test_negative_receipt_preserves_gear_and_records_actual_recipient(session) -> None:
    assignment, current, week = _coffer_assignment(session)
    confirm_loot_received(
        session,
        assignment.id,
        False,
        10,
        actual_recipient_character_id=assignment.intended_character_id,
    )
    assert current.current_classification is GearClassification.GARBAGE
    error = session.scalar(select(DistributionError))
    assert error.actual_recipient_id == assignment.intended_character_id
    assert assignment.state is LootAssignmentState.RECEIPT_FAILED


def test_redemption_before_receipt_and_missing_coffer_roll_back(session) -> None:
    assignment, current, week = _coffer_assignment(session)
    with pytest.raises(ConfirmationError, match="receipt confirmation"):
        confirm_coffer_redemption(session, assignment.id, True, 10)
    confirm_loot_received(session, assignment.id, True, 10)
    session.query(InventoryItem).delete()
    session.commit()
    with pytest.raises(ConfirmationError, match="inventory"):
        confirm_coffer_redemption(session, assignment.id, True, 10)
    assert session.scalar(select(func.count()).select_from(LootConfirmation)) == 1


def test_augmentation_receipt_and_application_consumes_one_material(session) -> None:
    assignment, current, week = _coffer_assignment(session)
    material = AugmentationMaterialType(raid_tier=week.raid_tier, code="POLISH", name="Polish")
    material_item = Item(name="Polish Item")
    material.item = material_item
    material_loot = LootType(
        raid_tier=week.raid_tier,
        code="POLISH_DROP",
        name="Polish Drop",
        category=LootCategory.AUGMENTATION_MATERIAL,
        item=material_item,
    )
    assignment.loot_type = material_loot
    assignment.intended_bis_set_item.classification = "AUGMENTED_TOME"
    assignment.intended_bis_set_item.augmentation_material_type = material
    assignment.resulting_classification = GearClassification.AUGMENTED_TOME
    current.current_classification = GearClassification.TOME
    session.commit()
    confirm_loot_received(session, assignment.id, True, 10)
    owned = session.scalar(select(CharacterAugmentationInventory))
    assert owned.quantity == 1
    confirm_augmentation_applied(session, assignment.id, True, 10)
    assert owned.quantity == 0
    assert current.current_classification is GearClassification.AUGMENTED_TOME


def test_pld_weapon_bundle_redemption_consumes_one_coffer_and_equips_both(session) -> None:
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    pld = fixture.mains[0]
    pld.job = session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    fixture.coffer.code = "WEAPON_COFFER"
    fixture.coffer.name = "Fictional Weapon Coffer"
    bis_set = BisSet(job=pld.job, raid_tier=fixture.tier, name="PLD Weapon Bundle")
    desired = {
        GearSlotCode.WEAPON: Item(name="Fictional PLD Sword"),
        GearSlotCode.OFFHAND: Item(name="Fictional PLD Shield"),
    }
    for code, slot in fixture.slots.items():
        if code in desired:
            bis_set.items.append(
                BisSetItem(
                    gear_slot=slot,
                    classification=GearClassification.SAVAGE,
                    raid_floor=fixture.floor,
                    loot_type=fixture.coffer,
                )
            )
        else:
            bis_set.items.append(
                BisSetItem(
                    gear_slot=slot,
                    classification=GearClassification.NOT_APPLICABLE,
                )
            )
    session.add(CharacterBisSelection(character=pld, raid_tier=fixture.tier, bis_set=bis_set))
    session.commit()
    result = generate_weekly_loot_plan(session, fixture.week.id)
    assignment = next(row.assignment for row in result.assignments if row.intended_recipient is pld)

    confirm_loot_received(session, assignment.id, True, 10)
    coffer = session.scalar(
        select(InventoryItem).where(
            InventoryItem.character_id == pld.id,
            InventoryItem.loot_type_id == fixture.coffer.id,
        )
    )
    assert coffer.quantity == 1
    confirm_coffer_redemption(session, assignment.id, True, 10)

    assert coffer.quantity == 0
    equipped = {
        row.gear_slot.code: row.current_classification
        for row in session.scalars(
            select(CharacterGearSlot).where(CharacterGearSlot.character_id == pld.id)
        )
    }
    assert equipped[GearSlotCode.WEAPON] is GearClassification.SAVAGE
    assert equipped[GearSlotCode.OFFHAND] is GearClassification.SAVAGE


def test_progress_and_closure_allow_errors_and_are_idempotent(session) -> None:
    assignment, current, week = _coffer_assignment(session)
    group = ReclearGroup(reclear_week=week, group_number=1)
    session.add(group)
    session.flush()
    assignment.reclear_group = group
    session.commit()
    mark_reclear_floors_complete(session, week.id, [(group.id, assignment.raid_floor_id)], 10)
    fixture_week = week
    # Directly seed the terminal error state to isolate progress/closure behavior.
    assignment.state = LootAssignmentState.RECEIPT_FAILED
    session.commit()
    progress = confirmation_progress(session, fixture_week.id)
    assert progress.total_planned_assignments == 1
    assert progress.failed_assignments == 1
    assert progress.can_close
    assert (
        close_reclear_week(session, fixture_week.id).workflow_state is ReclearWorkflowState.CLOSED
    )
    assert (
        close_reclear_week(session, fixture_week.id).workflow_state is ReclearWorkflowState.CLOSED
    )
    with pytest.raises(ConfirmationError, match="closed"):
        confirm_loot_received(session, assignment.id, True, 10)
