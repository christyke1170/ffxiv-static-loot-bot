"""Transactional reclear completion and loot confirmation services."""

from collections.abc import Iterable
from functools import wraps
from typing import ParamSpec, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    AuditLog,
    CharacterAugmentationInventory,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    ConfirmationQuestion,
    DistributionError,
    DistributionErrorType,
    GearClassification,
    InventoryItem,
    LootAssignment,
    LootAssignmentCompletionItem,
    LootAssignmentState,
    LootCategory,
    LootConfirmation,
    LootConfirmationType,
    LootPlan,
    ReclearFloorCompletion,
    ReclearGroup,
    ReclearWeek,
    ReclearWorkflowState,
    WeeklyLockout,
)
from app.schemas.confirmations import ConfirmationError, ConfirmationProgress, ConfirmationQueueItem
from app.services.transactions import entity_lock

P = ParamSpec("P")
R = TypeVar("R")


def _transactional(func):
    @wraps(func)
    def wrapped(session: Session, *args: P.args, **kwargs: P.kwargs) -> R:
        entity_id = int(args[0])
        kind = "week" if func.__name__ == "mark_reclear_floors_complete" else "assignment"
        with entity_lock(kind, entity_id), session.begin_nested():
            return func(session, *args, **kwargs)

    return wrapped


@_transactional
def mark_reclear_floors_complete(
    session: Session,
    reclear_week_id: int,
    completed_group_floors: Iterable[tuple[int, int]],
    actor_discord_user_id: int,
) -> list[ReclearFloorCompletion]:
    requested = list(dict.fromkeys(completed_group_floors))
    week = _week(session, reclear_week_id)
    if week.workflow_state in {
        ReclearWorkflowState.CLOSED,
        ReclearWorkflowState.CANCELLED,
    }:
        raise ConfirmationError("closed or cancelled reclear weeks cannot record floor completion")
    groups = {row.id: row for row in week.groups}
    floors = {row.id: row for row in week.raid_tier.floors}
    for group_id, floor_id in requested:
        group = groups.get(group_id)
        floor = floors.get(floor_id)
        if group is None:
            raise ConfirmationError(f"group {group_id} does not belong to reclear week {week.id}")
        if floor is None:
            raise ConfirmationError(
                f"floor {floor_id} does not belong to raid tier {week.raid_tier_id}"
            )
        existing = session.scalar(
            select(ReclearFloorCompletion).where(
                ReclearFloorCompletion.reclear_week_id == week.id,
                ReclearFloorCompletion.reclear_group_id == group_id,
                ReclearFloorCompletion.raid_floor_id == floor_id,
            )
        )
        if existing:
            continue
        session.add(
            ReclearFloorCompletion(
                reclear_week=week,
                reclear_group=group,
                raid_floor=floor,
                actor_discord_user_id=actor_discord_user_id,
            )
        )
        for participant in group.participants:
            _lockout(session, participant.character_id, floor_id, week.week_start)
            _book(session, participant.character_id, floor_id)
    if requested:
        week.workflow_state = ReclearWorkflowState.AWAITING_CONFIRMATION
    session.flush()
    return list(
        session.scalars(
            select(ReclearFloorCompletion)
            .where(ReclearFloorCompletion.reclear_week_id == week.id)
            .order_by(ReclearFloorCompletion.raid_floor_id, ReclearFloorCompletion.reclear_group_id)
        )
    )


def confirmation_queue(session: Session, reclear_week_id: int) -> list[ConfirmationQueueItem]:
    result = []
    for row in _assignments(session, reclear_week_id):
        question = _next_question(row)
        if question:
            result.append(ConfirmationQueueItem(row, question))
    return result


@_transactional
def confirm_loot_received(
    session: Session,
    assignment_id: int,
    received: bool,
    actor_discord_user_id: int,
    note: str | None = None,
    actual_recipient_character_id: int | None = None,
) -> LootAssignment:
    row = _assignment(session, assignment_id)
    _require_week_open(row)
    current = _answer(row, ConfirmationQuestion.RECEIVED)
    if current is not None:
        if current == received:
            return row
        raise ConfirmationError("receipt was already answered with the opposite result")
    recipient_id = row.final_recipient_id or row.intended_character_id
    if recipient_id is None:
        raise ConfirmationError("an unassigned assignment cannot be confirmed")
    _append(row, LootConfirmationType.RECEIVED, received, actor_discord_user_id, note)
    if not received:
        row.state = LootAssignmentState.RECEIPT_FAILED
        session.add(
            DistributionError(
                reclear_week_id=row.loot_plan.reclear_week_id,
                loot_assignment=row,
                intended_recipient_id=recipient_id,
                actual_recipient_id=actual_recipient_character_id,
                error_type=(
                    DistributionErrorType.WRONG_RECIPIENT
                    if actual_recipient_character_id
                    else DistributionErrorType.INTENDED_RECIPIENT_DID_NOT_RECEIVE
                ),
                description=note or "The intended recipient did not receive the planned loot.",
                reported_by_discord_user_id=actor_discord_user_id,
            )
        )
    else:
        row.state = LootAssignmentState.RECEIVED
        if row.loot_type.category is LootCategory.COFFER:
            _add_inventory(session, recipient_id, row.loot_type.item_id)
        elif row.loot_type.category is LootCategory.AUGMENTATION_MATERIAL:
            _add_material(
                session,
                recipient_id,
                row.intended_bis_set_item.augmentation_material_type_id,
            )
        else:
            confirmation = row.confirmations[-1]
            confirmation.previous_gear_classification = _equip(session, row)
            row.state = LootAssignmentState.REDEEMED_CORRECTLY
        from app.models import LootReceipt

        session.add(
            LootReceipt(
                assignment=row,
                item_id=(row.intended_final_item_id or row.loot_type.item_id),
                quantity=row.quantity,
            )
        )
    _audit(session, row, "LOOT_RECEIPT_CONFIRMED", actor_discord_user_id, note)
    session.flush()
    return row


@_transactional
def confirm_coffer_redemption(
    session: Session,
    assignment_id: int,
    redeemed_correctly: bool,
    actor_discord_user_id: int,
    note: str | None = None,
) -> LootAssignment:
    row = _assignment(session, assignment_id)
    _require_week_open(row)
    _require_received(row, LootCategory.COFFER)
    if not _dependent_answer(
        row,
        LootConfirmationType.REDEEMED_CORRECTLY,
        redeemed_correctly,
        actor_discord_user_id,
        note,
    ):
        return row
    recipient_id = row.final_recipient_id or row.intended_character_id
    _remove_inventory(session, recipient_id, row.loot_type.item_id, 1)
    if redeemed_correctly:
        _equip_completion_items(session, row, row.confirmations[-1])
        row.state = LootAssignmentState.REDEEMED_CORRECTLY
    else:
        row.state = LootAssignmentState.REDEMPTION_ERROR
        session.add(
            DistributionError(
                reclear_week_id=row.loot_plan.reclear_week_id,
                loot_assignment=row,
                intended_recipient_id=row.intended_character_id,
                error_type=DistributionErrorType.WRONG_COFFER_REDEMPTION,
                description=note or "The received coffer was redeemed for the wrong item.",
                reported_by_discord_user_id=actor_discord_user_id,
            )
        )
    _audit(session, row, "COFFER_REDEMPTION_CONFIRMED", actor_discord_user_id, note)
    session.flush()
    return row


@_transactional
def confirm_augmentation_applied(
    session: Session,
    assignment_id: int,
    applied_correctly: bool,
    actor_discord_user_id: int,
    note: str | None = None,
) -> LootAssignment:
    row = _assignment(session, assignment_id)
    _require_week_open(row)
    _require_received(row, LootCategory.AUGMENTATION_MATERIAL)
    if not _dependent_answer(
        row, LootConfirmationType.AUGMENT_APPLIED, applied_correctly, actor_discord_user_id, note
    ):
        return row
    requirement = row.intended_bis_set_item
    if requirement is None:
        raise ConfirmationError("assignment has no intended augmentation requirement")
    if applied_correctly and requirement.base_tome_item_id is None:
        raise ConfirmationError("assignment has no intended base tome item")
    recipient_id = row.final_recipient_id or row.intended_character_id
    if applied_correctly and not _owns_item(session, recipient_id, requirement.base_tome_item_id):
        raise ConfirmationError("intended recipient does not own the required base tome item")
    _remove_material(session, recipient_id, requirement.augmentation_material_type_id, 1)
    if applied_correctly:
        _remove_inventory(session, recipient_id, requirement.base_tome_item_id, 1)
        _equip(session, row)
        row.state = LootAssignmentState.REDEEMED_CORRECTLY
    else:
        row.state = LootAssignmentState.REDEMPTION_ERROR
        session.add(
            DistributionError(
                reclear_week_id=row.loot_plan.reclear_week_id,
                loot_assignment=row,
                intended_recipient_id=row.intended_character_id,
                error_type=DistributionErrorType.AUGMENT_NOT_APPLIED,
                description=note or "The augmentation material was used incorrectly.",
                reported_by_discord_user_id=actor_discord_user_id,
            )
        )
    _audit(session, row, "AUGMENTATION_CONFIRMED", actor_discord_user_id, note)
    session.flush()
    return row


def confirmation_progress(session: Session, reclear_week_id: int) -> ConfirmationProgress:
    rows = _assignments(session, reclear_week_id)
    questions = [_next_question(row) for row in rows]
    failed = sum(
        row.state in {LootAssignmentState.RECEIPT_FAILED, LootAssignmentState.REDEMPTION_ERROR}
        for row in rows
    )
    leftovers = sum(
        row.state in {LootAssignmentState.LEFTOVER, LootAssignmentState.FREE_ROLL} for row in rows
    )
    return ConfirmationProgress(
        len(rows),
        sum(item is None for item in questions),
        sum(item is ConfirmationQuestion.RECEIVED for item in questions),
        sum(item is ConfirmationQuestion.REDEEMED_CORRECTLY for item in questions),
        sum(item is ConfirmationQuestion.AUGMENT_APPLIED for item in questions),
        failed,
        leftovers,
        all(item is None for item in questions),
    )


def close_reclear_week(session: Session, reclear_week_id: int) -> ReclearWeek:
    week = _week(session, reclear_week_id)
    if week.workflow_state is ReclearWorkflowState.CLOSED:
        return week
    if not confirmation_progress(session, reclear_week_id).can_close:
        raise ConfirmationError("reclear week still has pending confirmation questions")
    week.workflow_state = ReclearWorkflowState.CLOSED
    session.flush()
    return week


@_transactional
def correct_confirmation(
    session: Session,
    assignment_id: int,
    question: ConfirmationQuestion,
    result: bool,
    actor_discord_user_id: int,
    note: str | None = None,
) -> LootAssignment:
    row = _assignment(session, assignment_id)
    if row.loot_plan.reclear_week.workflow_state is ReclearWorkflowState.CLOSED:
        raise ConfirmationError("manual intervention required: the reclear week is closed")
    old = _answer(row, question)
    if old is None:
        raise ConfirmationError("there is no prior answer to correct")
    if old == result:
        return row
    if question is ConfirmationQuestion.REDEEMED_CORRECTLY:
        _correct_redemption(row, old, result, actor_discord_user_id, note, session)
        session.flush()
        return row
    dependent_answers = {
        ConfirmationQuestion.REDEEMED_CORRECTLY,
        ConfirmationQuestion.AUGMENT_APPLIED,
    }
    if question is not ConfirmationQuestion.RECEIVED or any(
        _answer(row, item) is not None for item in dependent_answers
    ):
        raise ConfirmationError(
            "manual intervention required: later dependent actions prevent safe reversal"
        )
    previous = next(
        item
        for item in reversed(row.confirmations)
        if item.confirmation_type.value == question.value
    )
    row.confirmations.append(
        LootConfirmation(
            confirmation_type=LootConfirmationType.RECEIVED,
            result=result,
            answered_by_discord_user_id=actor_discord_user_id,
            note=note,
            supersedes=previous,
        )
    )
    recipient_id = row.final_recipient_id or row.intended_character_id
    if old and not result:
        if row.loot_type.category is LootCategory.COFFER:
            _remove_inventory(session, recipient_id, row.loot_type.item_id, 1)
        elif row.loot_type.category is LootCategory.AUGMENTATION_MATERIAL:
            _remove_material(
                session,
                recipient_id,
                row.intended_bis_set_item.augmentation_material_type_id,
                1,
            )
        else:
            _restore_gear(session, row, previous)
        if row.receipt:
            session.delete(row.receipt)
        row.state = LootAssignmentState.RECEIPT_FAILED
        error = session.scalar(
            select(DistributionError).where(
                DistributionError.loot_assignment_id == row.id,
                DistributionError.resolved.is_(False),
            )
        )
        if error:
            error.resolved = True
            error.resolution_note = "Superseded by corrected receipt answer."
        session.add(
            DistributionError(
                reclear_week_id=row.loot_plan.reclear_week_id,
                loot_assignment=row,
                intended_recipient_id=recipient_id,
                error_type=DistributionErrorType.INTENDED_RECIPIENT_DID_NOT_RECEIVE,
                description=note or "The intended recipient did not receive the planned loot.",
                reported_by_discord_user_id=actor_discord_user_id,
            )
        )
    elif not old and result:
        error = session.scalar(
            select(DistributionError).where(
                DistributionError.loot_assignment_id == row.id,
                DistributionError.resolved.is_(False),
            )
        )
        if error:
            error.resolved = True
            error.resolution_note = "Superseded by corrected receipt answer."
        _equip_or_inventory(session, row)
        row.state = (
            LootAssignmentState.REDEEMED_CORRECTLY
            if row.loot_type.category is LootCategory.GEAR
            else LootAssignmentState.RECEIVED
        )
        from app.models import LootReceipt

        session.add(
            LootReceipt(
                assignment=row,
                item_id=(row.intended_final_item_id or row.loot_type.item_id),
                quantity=row.quantity,
            )
        )
    _audit(session, row, "LOOT_CONFIRMATION_CORRECTED", actor_discord_user_id, note)
    session.flush()
    return row


def _correct_redemption(row, old, result, actor, note, session):
    if row.loot_type.category is not LootCategory.COFFER:
        raise ConfirmationError("manual intervention required: assignment is not a coffer")
    previous = next(
        item
        for item in reversed(row.confirmations)
        if item.confirmation_type is LootConfirmationType.REDEEMED_CORRECTLY
    )
    row.confirmations.append(
        LootConfirmation(
            confirmation_type=LootConfirmationType.REDEEMED_CORRECTLY,
            result=result,
            answered_by_discord_user_id=actor,
            note=note,
            supersedes=previous,
        )
    )
    error = session.scalar(
        select(DistributionError).where(
            DistributionError.loot_assignment_id == row.id,
            DistributionError.resolved.is_(False),
        )
    )
    if error:
        error.resolved = True
        error.resolution_note = "Superseded by corrected redemption answer."
    if result:
        _equip_completion_items(session, row, row.confirmations[-1])
        row.state = LootAssignmentState.REDEEMED_CORRECTLY
    else:
        _restore_completion_items(session, row, previous)
        row.state = LootAssignmentState.REDEMPTION_ERROR
        session.add(
            DistributionError(
                reclear_week_id=row.loot_plan.reclear_week_id,
                loot_assignment=row,
                intended_recipient_id=(row.final_recipient_id or row.intended_character_id),
                error_type=DistributionErrorType.WRONG_COFFER_REDEMPTION,
                description=note or "The received coffer was redeemed for the wrong item.",
                reported_by_discord_user_id=actor,
            )
        )


def _week(session: Session, id_: int) -> ReclearWeek:
    row = session.scalar(
        select(ReclearWeek)
        .where(ReclearWeek.id == id_)
        .with_for_update()
        .options(
            selectinload(ReclearWeek.groups).selectinload(ReclearGroup.participants),
            joinedload(ReclearWeek.raid_tier),
        )
    )
    if row is None:
        raise ConfirmationError(f"unknown reclear week id {id_}")
    return row


def _assignment(session: Session, id_: int) -> LootAssignment:
    row = session.scalar(
        select(LootAssignment)
        .where(LootAssignment.id == id_)
        .with_for_update()
        .options(
            joinedload(LootAssignment.loot_plan),
            joinedload(LootAssignment.loot_type),
            joinedload(LootAssignment.intended_bis_set_item),
            selectinload(LootAssignment.completion_items).joinedload(
                LootAssignmentCompletionItem.bis_set_item
            ),
            joinedload(LootAssignment.final_recipient),
            selectinload(LootAssignment.confirmations),
            joinedload(LootAssignment.receipt),
        )
    )
    if row is None:
        raise ConfirmationError(f"unknown loot assignment id {id_}")
    return row


def _assignments(session: Session, week_id: int) -> list[LootAssignment]:
    rows = list(
        session.scalars(
            select(LootAssignment)
            .join(LootPlan)
            .where(LootPlan.reclear_week_id == week_id)
            .options(
                joinedload(LootAssignment.raid_floor),
                joinedload(LootAssignment.reclear_group),
                joinedload(LootAssignment.loot_type),
                selectinload(LootAssignment.confirmations),
                joinedload(LootAssignment.receipt),
            )
            .order_by(
                LootAssignment.raid_floor_id,
                LootAssignment.reclear_group_id,
                LootAssignment.sort_order,
                LootAssignment.expected_drop_instance,
            )
        )
    )
    completed = {
        (row.reclear_group_id, row.raid_floor_id)
        for row in session.scalars(
            select(ReclearFloorCompletion).where(ReclearFloorCompletion.reclear_week_id == week_id)
        )
    }
    return [row for row in rows if (row.reclear_group_id, row.raid_floor_id) in completed]


def _answer(row: LootAssignment, question: ConfirmationQuestion) -> bool | None:
    values = [
        item.result for item in row.confirmations if item.confirmation_type.value == question.value
    ]
    return values[-1] if values else None


def _next_question(row: LootAssignment) -> ConfirmationQuestion | None:
    if row.state in {
        LootAssignmentState.LEFTOVER,
        LootAssignmentState.CANCELLED,
        LootAssignmentState.RECEIPT_FAILED,
        LootAssignmentState.REDEMPTION_ERROR,
        LootAssignmentState.REDEEMED_CORRECTLY,
    }:
        return None
    if row.state is LootAssignmentState.FREE_ROLL and row.intended_character_id is None:
        return None
    if _answer(row, ConfirmationQuestion.RECEIVED) is None:
        return ConfirmationQuestion.RECEIVED
    if (
        row.loot_type.category is LootCategory.COFFER
        and _answer(row, ConfirmationQuestion.REDEEMED_CORRECTLY) is None
    ):
        return ConfirmationQuestion.REDEEMED_CORRECTLY
    if (
        row.loot_type.category is LootCategory.AUGMENTATION_MATERIAL
        and _answer(row, ConfirmationQuestion.AUGMENT_APPLIED) is None
    ):
        return ConfirmationQuestion.AUGMENT_APPLIED
    return None


def _append(row, kind, result, actor, note):
    row.confirmations.append(
        LootConfirmation(
            confirmation_type=kind, result=result, answered_by_discord_user_id=actor, note=note
        )
    )


def _dependent_answer(row, kind, result, actor, note):
    current = _answer(row, ConfirmationQuestion(kind.value))
    if current is not None:
        if current != result:
            raise ConfirmationError("confirmation was already answered with the opposite result")
        return False
    _append(row, kind, result, actor, note)
    return True


def _require_received(row, category):
    if (
        row.loot_type.category is not category
        or _answer(row, ConfirmationQuestion.RECEIVED) is not True
    ):
        raise ConfirmationError("dependent confirmation requires receipt confirmation Yes")


def _require_week_open(row: LootAssignment) -> None:
    if row.loot_plan.reclear_week.workflow_state in {
        ReclearWorkflowState.CLOSED,
        ReclearWorkflowState.CANCELLED,
    }:
        raise ConfirmationError("closed or cancelled reclear weeks reject ordinary confirmations")


def _lockout(session, character_id, floor_id, week_start):
    row = session.scalar(
        select(WeeklyLockout).where(
            WeeklyLockout.character_id == character_id,
            WeeklyLockout.raid_floor_id == floor_id,
            WeeklyLockout.week_start == week_start,
        )
    )
    if row is None:
        session.add(
            WeeklyLockout(
                character_id=character_id,
                raid_floor_id=floor_id,
                week_start=week_start,
                cleared=True,
            )
        )
    else:
        row.cleared = True


def _book(session, character_id, floor_id):
    row = session.scalar(
        select(CharacterFloorBookBalance).where(
            CharacterFloorBookBalance.character_id == character_id,
            CharacterFloorBookBalance.raid_floor_id == floor_id,
        )
    )
    if row is None:
        session.add(
            CharacterFloorBookBalance(character_id=character_id, raid_floor_id=floor_id, earned=1)
        )
    else:
        row.earned += 1


def _add_inventory(session, character_id, item_id):
    if item_id is None:
        raise ConfirmationError("loot type has no inventory item")
    row = session.scalar(
        select(InventoryItem).where(
            InventoryItem.character_id == character_id, InventoryItem.item_id == item_id
        )
    )
    if row is None:
        session.add(InventoryItem(character_id=character_id, item_id=item_id, quantity=1))
    else:
        row.quantity += 1


def _remove_inventory(session, character_id, item_id, quantity):
    row = session.scalar(
        select(InventoryItem).where(
            InventoryItem.character_id == character_id, InventoryItem.item_id == item_id
        )
    )
    if row is None or row.quantity < quantity:
        raise ConfirmationError("expected inventory quantity is missing")
    row.quantity -= quantity


def _add_material(session, character_id, material_id):
    row = session.scalar(
        select(CharacterAugmentationInventory).where(
            CharacterAugmentationInventory.character_id == character_id,
            CharacterAugmentationInventory.augmentation_material_type_id == material_id,
        )
    )
    if row is None:
        session.add(
            CharacterAugmentationInventory(
                character_id=character_id, augmentation_material_type_id=material_id, quantity=1
            )
        )
    else:
        row.quantity += 1


def _remove_material(session, character_id, material_id, quantity):
    row = session.scalar(
        select(CharacterAugmentationInventory).where(
            CharacterAugmentationInventory.character_id == character_id,
            CharacterAugmentationInventory.augmentation_material_type_id == material_id,
        )
    )
    if row is None or row.quantity < quantity:
        raise ConfirmationError("expected augmentation material quantity is missing")
    row.quantity -= quantity


def _owns_item(session, character_id, item_id):
    return bool(
        session.scalar(
            select(InventoryItem.id).where(
                InventoryItem.character_id == character_id,
                InventoryItem.item_id == item_id,
                InventoryItem.quantity > 0,
            )
        )
    )


def _equip(session, row):
    requirement = row.intended_bis_set_item
    if requirement is None or row.intended_final_item_id is None:
        raise ConfirmationError("assignment has no exact intended gear slot and item")
    recipient_id = row.final_recipient_id or row.intended_character_id
    classification = GearClassification(requirement.classification)
    slot = session.scalar(
        select(CharacterGearSlot).where(
            CharacterGearSlot.character_id == recipient_id,
            CharacterGearSlot.gear_slot_id == requirement.gear_slot_id,
        )
    )
    previous_classification = slot.current_classification if slot is not None else None
    if slot is None:
        session.add(
            CharacterGearSlot(
                character_id=recipient_id,
                gear_slot_id=requirement.gear_slot_id,
                current_classification=classification,
            )
        )
    else:
        slot.current_classification = classification
        slot.manually_complete = False
    return previous_classification


def _equip_completion_items(session, row, confirmation):
    """Equip every completion target while consuming only the assignment's one drop."""
    if not row.completion_items:
        confirmation.previous_gear_classification = _equip(session, row)
        return
    recipient_id = row.final_recipient_id or row.intended_character_id
    for target in row.completion_items:
        classification = GearClassification(target.bis_set_item.classification)
        slot = session.scalar(
            select(CharacterGearSlot).where(
                CharacterGearSlot.character_id == recipient_id,
                CharacterGearSlot.gear_slot_id == target.bis_set_item.gear_slot_id,
            )
        )
        target.previous_gear_classification = (
            slot.current_classification if slot is not None else None
        )
        if slot is None:
            session.add(
                CharacterGearSlot(
                    character_id=recipient_id,
                    gear_slot_id=target.bis_set_item.gear_slot_id,
                    current_classification=classification,
                )
            )
        else:
            slot.current_classification = classification
            slot.manually_complete = False


def _restore_completion_items(session, row, confirmation):
    if not row.completion_items:
        _restore_gear(session, row, confirmation)
        return
    recipient_id = row.final_recipient_id or row.intended_character_id
    for target in row.completion_items:
        slot = session.scalar(
            select(CharacterGearSlot).where(
                CharacterGearSlot.character_id == recipient_id,
                CharacterGearSlot.gear_slot_id == target.bis_set_item.gear_slot_id,
            )
        )
        if target.previous_gear_classification is None:
            if slot is not None:
                session.delete(slot)
        elif slot is None:
            session.add(
                CharacterGearSlot(
                    character_id=recipient_id,
                    gear_slot_id=target.bis_set_item.gear_slot_id,
                    current_classification=target.previous_gear_classification,
                )
            )
        else:
            slot.current_classification = target.previous_gear_classification
            slot.manually_complete = False


def _equip_or_inventory(session, row):
    recipient_id = row.final_recipient_id or row.intended_character_id
    if row.loot_type.category is LootCategory.COFFER:
        _add_inventory(session, recipient_id, row.loot_type.item_id)
    elif row.loot_type.category is LootCategory.AUGMENTATION_MATERIAL:
        _add_material(
            session,
            recipient_id,
            row.intended_bis_set_item.augmentation_material_type_id,
        )
    else:
        _equip(session, row)


def _remove_gear(session, row):
    requirement = row.intended_bis_set_item
    recipient_id = row.final_recipient_id or row.intended_character_id
    if requirement:
        slot = session.scalar(
            select(CharacterGearSlot).where(
                CharacterGearSlot.character_id == recipient_id,
                CharacterGearSlot.gear_slot_id == requirement.gear_slot_id,
            )
        )
        if slot and slot.current_classification == GearClassification(requirement.classification):
            session.delete(slot)


def _restore_gear(session, row, confirmation):
    requirement = row.intended_bis_set_item
    if requirement is None:
        raise ConfirmationError("manual intervention required: missing intended gear slot")
    recipient_id = row.final_recipient_id or row.intended_character_id
    slot = session.scalar(
        select(CharacterGearSlot).where(
            CharacterGearSlot.character_id == recipient_id,
            CharacterGearSlot.gear_slot_id == requirement.gear_slot_id,
        )
    )
    if confirmation.previous_gear_classification is None:
        if slot is not None:
            session.delete(slot)
    elif slot is None:
        session.add(
            CharacterGearSlot(
                character_id=recipient_id,
                gear_slot_id=requirement.gear_slot_id,
                current_classification=confirmation.previous_gear_classification,
            )
        )
    else:
        slot.current_classification = confirmation.previous_gear_classification
        slot.manually_complete = False


def _audit(session, row, action, actor, note):
    session.add(
        AuditLog(
            static_id=row.loot_plan.reclear_week.static_id,
            actor_discord_user_id=actor,
            action=action,
            entity_type="LootAssignment",
            entity_id=str(row.id),
            details=note,
        )
    )
