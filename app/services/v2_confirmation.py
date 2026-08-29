"""Transactional confirmation and application for persisted neutral V2 plans."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.models import (
    CharacterGearSlot,
    GearClassification,
    GearSlot,
    ReclearWorkflowState,
    V2Confirmation,
    V2Correction,
    V2EffectLedger,
    V2Plan,
    V2PlanAssignment,
    V2PlanRun,
    V2ResourceBalance,
)
from app.schemas.v2_confirmation import (
    V2ConfirmationReadback,
    V2ConfirmationState,
    V2CorrectionState,
    V2EffectState,
)
from app.services.neutral_resources import adjust_current_balance, validate_resource_key


class V2ConfirmationError(ValueError):
    """A V2 confirmation or application request is invalid."""


def confirm_v2_receipt(
    session,
    assignment_id: int,
    resource_key: str,
    success: bool,
    actor_id: int | None = None,
    recipient_id: int | None = None,
    quantity: int = 1,
    note: str | None = None,
    static_id: int | None = None,
    week_id: int | None = None,
) -> V2ConfirmationState:
    assignment = _assignment(session, assignment_id)
    _validate_scope(assignment, static_id, week_id)
    recipient = _recipient(assignment, recipient_id)
    _validate_active(assignment)
    existing = _existing(session, assignment_id, resource_key, "RECEIPT")
    if existing is not None:
        if (existing.success, existing.recipient_id, existing.quantity) != (
            success,
            recipient,
            quantity,
        ):
            raise V2ConfirmationError("A conflicting receipt confirmation already exists.")
        return _state(existing)
    if quantity <= 0:
        raise V2ConfirmationError("Confirmation quantity must be positive.")
    try:
        with session.begin_nested():
            row = V2Confirmation(
                assignment_id=assignment_id,
                resource_key=resource_key,
                action="RECEIPT",
                success=success,
                recipient_id=recipient,
                quantity=quantity,
                actor_id=actor_id,
                note=note,
            )
            session.add(row)
            if success and recipient is not None:
                _balance(session, assignment.plan_id, recipient, resource_key, quantity)
            session.flush()
    except IntegrityError:
        existing = _existing(session, assignment_id, resource_key, "RECEIPT")
        if existing is None:
            raise
        if (existing.success, existing.recipient_id, existing.quantity) != (
            success,
            recipient,
            quantity,
        ):
            raise V2ConfirmationError(
                "A conflicting receipt confirmation already exists."
            ) from None
        return _state(existing)
    return _state(row)


def confirm_v2_application(
    session,
    assignment_id: int,
    success: bool,
    actor_id: int | None = None,
    recipient_id: int | None = None,
    note: str | None = None,
    static_id: int | None = None,
    week_id: int | None = None,
) -> V2ConfirmationState:
    assignment = _assignment(session, assignment_id)
    _validate_scope(assignment, static_id, week_id)
    recipient = _recipient(assignment, recipient_id)
    _validate_active(assignment)
    if assignment.disposition == "FREE_ROLL":
        raise V2ConfirmationError("Free-for-all assignments do not have an application action.")
    existing = _existing(session, assignment_id, "APPLICATION", "APPLICATION")
    if existing is not None:
        if _effective_success(session, existing) != success:
            if (
                success
                and existing.action == "APPLICATION"
                and _latest_correction(session, existing.id, "APPLICATION_REVERSAL") is not None
            ):
                correct_v2_application(
                    session, existing.id, True, actor_id or 0, note or "reapplication"
                )
                return _state(existing)
            raise V2ConfirmationError("A conflicting application confirmation already exists.")
        return _state(existing)
    if not success:
        with session.begin_nested():
            row = _record_application(session, assignment, recipient, False, actor_id, note)
            session.flush()
        return _state(row)
    # Materials are grants, not redeemable gear resources.
    if assignment.material_key is not None:
        raise V2ConfirmationError("Material grants do not have an application action.")
    if _is_paired_tome(assignment):
        for resource in ("WEAPON_TOMESTONE", "WEAPON_AUGMENT"):
            receipt = _existing(session, assignment.id, resource, "RECEIPT")
            if receipt is None or not receipt.success:
                raise V2ConfirmationError(
                    "Both paired Tome resources must be successfully received first."
                )
    else:
        receipt = _existing(
            session, assignment.id, assignment.material_key or assignment.loot_key, "RECEIPT"
        )
        if receipt is None or not receipt.success:
            raise V2ConfirmationError("The assignment must be successfully received first.")
    with session.begin_nested():
        row = _record_application(session, assignment, recipient, True, actor_id, note)
        _apply_assignment(session, assignment, recipient, row)
        session.flush()
    return _state(row)


def read_v2_confirmation_state(session, assignment_id: int) -> V2ConfirmationReadback:
    assignment = _assignment(session, assignment_id)
    confirmations = tuple(
        _state(row)
        for row in session.scalars(
            select(V2Confirmation)
            .where(V2Confirmation.assignment_id == assignment_id)
            .order_by(V2Confirmation.id)
        )
    )
    effects = tuple(
        V2EffectState(
            row.id,
            row.confirmation_id,
            row.recipient_id,
            row.slot_key,
            row.resulting_category,
            row.before_category,
            row.after_category,
            row.quantity_delta,
        )
        for row in session.scalars(
            select(V2EffectLedger)
            .join(V2Confirmation)
            .where(V2Confirmation.assignment_id == assignment_id)
            .order_by(V2EffectLedger.id)
        )
    )
    readback_recipient = next(
        (row.recipient_id for row in confirmations if row.recipient_id is not None),
        assignment.recipient_id,
    )
    balances = (
        tuple(
            (row.resource_key, row.quantity)
            for row in session.scalars(
                select(V2ResourceBalance)
                .where(
                    V2ResourceBalance.static_id == assignment.run.plan.static_id,
                    V2ResourceBalance.recipient_id == readback_recipient,
                )
                .order_by(V2ResourceBalance.resource_key)
            )
        )
        if readback_recipient is not None
        else ()
    )
    return V2ConfirmationReadback(assignment_id, confirmations, effects, balances)


def correct_v2_receipt(
    session, confirmation_id: int, corrected_success: bool, actor_id: int, reason: str
) -> V2CorrectionState:
    confirmation = _confirmation(session, confirmation_id)
    if confirmation.action != "RECEIPT":
        raise V2ConfirmationError("Only receipt confirmations can receive receipt corrections.")
    _require_admin_reason(actor_id, reason)
    current = _effective_success(session, confirmation)
    if current == corrected_success:
        existing = _latest_correction(session, confirmation.id, "RECEIPT_OUTCOME")
        if existing is not None:
            return _correction_state(existing)
        raise V2ConfirmationError("Receipt already has the requested effective outcome.")
    if (
        current
        and not corrected_success
        and _active_application(session, confirmation.assignment_id)
    ):
        raise V2ConfirmationError(
            "Reverse the active application before correcting receipt failure."
        )
    with session.begin_nested():
        row = V2Correction(
            confirmation_id=confirmation.id,
            correction_type="RECEIPT_OUTCOME",
            corrected_success=corrected_success,
            actor_id=actor_id,
            reason=reason.strip(),
        )
        session.add(row)
        _balance(
            session,
            confirmation.assignment.plan_id,
            confirmation.recipient_id,
            confirmation.resource_key,
            confirmation.quantity if corrected_success else -confirmation.quantity,
        )
        session.flush()
    return _correction_state(row)


def correct_v2_application(
    session, confirmation_id: int, corrected_success: bool, actor_id: int, reason: str
) -> V2CorrectionState:
    confirmation = _confirmation(session, confirmation_id)
    if confirmation.action != "APPLICATION":
        raise V2ConfirmationError(
            "Only application confirmations can receive application corrections."
        )
    _require_admin_reason(actor_id, reason)
    current = _effective_success(session, confirmation)
    if current == corrected_success:
        existing = _latest_correction(session, confirmation.id, "APPLICATION_OUTCOME")
        if existing is not None:
            return _correction_state(existing)
        raise V2ConfirmationError("Application already has the requested effective outcome.")
    if corrected_success:
        with session.begin_nested():
            row = V2Correction(
                confirmation_id=confirmation.id,
                correction_type="APPLICATION_OUTCOME",
                corrected_success=True,
                actor_id=actor_id,
                reason=reason.strip(),
            )
            session.add(row)
            _apply_assignment(
                session, confirmation.assignment, confirmation.recipient_id, confirmation
            )
            session.flush()
        return _correction_state(row)
    return reverse_v2_application(session, confirmation_id, actor_id, reason)


def reverse_v2_application(
    session, confirmation_id: int, actor_id: int, reason: str
) -> V2CorrectionState:
    confirmation = _confirmation(session, confirmation_id)
    _require_admin_reason(actor_id, reason)
    existing = _latest_correction(session, confirmation.id, "APPLICATION_REVERSAL")
    if existing is not None:
        return _correction_state(existing)
    if confirmation.action != "APPLICATION" or not _effective_success(session, confirmation):
        raise V2ConfirmationError("Only an effective successful application can be reversed.")
    with session.begin_nested():
        _restore_application(session, confirmation)
        row = V2Correction(
            confirmation_id=confirmation.id,
            correction_type="APPLICATION_REVERSAL",
            corrected_success=False,
            actor_id=actor_id,
            reason=reason.strip(),
        )
        session.add(row)
        session.flush()
    return _correction_state(row)


def read_v2_correction_history(session, assignment_id: int) -> tuple[V2CorrectionState, ...]:
    _assignment(session, assignment_id)
    return tuple(
        _correction_state(row)
        for row in session.scalars(
            select(V2Correction)
            .join(V2Confirmation)
            .where(V2Confirmation.assignment_id == assignment_id)
            .order_by(V2Correction.id)
        )
    )


def _assignment(session, assignment_id):
    row = session.scalar(
        select(V2PlanAssignment)
        .where(V2PlanAssignment.id == assignment_id)
        .options(
            joinedload(V2PlanAssignment.run)
            .joinedload(V2PlanRun.plan)
            .joinedload(V2Plan.reclear_week),
            selectinload(V2PlanAssignment.effects),
        )
    )
    if row is None:
        raise V2ConfirmationError(f"V2 assignment {assignment_id} was not found.")
    return row


def _validate_active(assignment):
    if not assignment.run.plan.static.active:
        raise V2ConfirmationError("Inactive statics cannot record V2 confirmations.")
    week = assignment.run.plan.reclear_week
    if week.workflow_state in {ReclearWorkflowState.CANCELLED, ReclearWorkflowState.CLOSED}:
        raise V2ConfirmationError("cancelled or closed V2 plans cannot be confirmed.")


def _validate_scope(assignment, static_id, week_id):
    plan = assignment.plan
    if static_id is not None and plan.static_id != static_id:
        raise V2ConfirmationError("Assignment does not belong to the requested static.")
    if week_id is not None and plan.reclear_week_id != week_id:
        raise V2ConfirmationError("Assignment does not belong to the requested week.")


def _is_paired_tome(assignment):
    return "TOME" in assignment.loot_key.upper() and len(assignment.effects) >= 1


def _recipient(assignment, recipient_id):
    recipient_id = assignment.recipient_id if recipient_id is None else recipient_id
    if recipient_id is None:
        raise V2ConfirmationError("A free-for-all assignment requires an explicit recipient.")
    participant_ids = {row.character_id for row in assignment.run.participants}
    if recipient_id not in participant_ids:
        raise V2ConfirmationError("Recipient is not in the assignment's run.")
    return recipient_id


def _existing(session, assignment_id, resource_key, action):
    return session.scalar(
        select(V2Confirmation).where(
            V2Confirmation.assignment_id == assignment_id,
            V2Confirmation.resource_key == resource_key,
            V2Confirmation.action == action,
        )
    )


def _confirmation(session, confirmation_id):
    statement = select(V2Confirmation).where(V2Confirmation.id == confirmation_id)
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise V2ConfirmationError(f"V2 confirmation {confirmation_id} was not found.")
    return row


def _require_admin_reason(actor_id, reason):
    if actor_id is None:
        raise V2ConfirmationError("An administrator actor identity is required.")
    if not reason or not reason.strip():
        raise V2ConfirmationError("A non-empty correction reason is required.")


def _latest_correction(session, confirmation_id, correction_type):
    return session.scalar(
        select(V2Correction)
        .where(
            V2Correction.confirmation_id == confirmation_id,
            V2Correction.correction_type == correction_type,
        )
        .order_by(V2Correction.id.desc())
    )


def _effective_success(session, confirmation):
    if confirmation.action == "APPLICATION":
        correction = session.scalar(
            select(V2Correction)
            .where(
                V2Correction.confirmation_id == confirmation.id,
                V2Correction.correction_type.in_(("APPLICATION_OUTCOME", "APPLICATION_REVERSAL")),
            )
            .order_by(V2Correction.id.desc())
        )
    else:
        correction = _latest_correction(session, confirmation.id, "RECEIPT_OUTCOME")
    if correction is not None:
        return correction.corrected_success
    return confirmation.success


def _active_application(session, assignment_id):
    application = session.scalar(
        select(V2Confirmation).where(
            V2Confirmation.assignment_id == assignment_id,
            V2Confirmation.action == "APPLICATION",
        )
    )
    return application is not None and _effective_success(session, application)


def _restore_application(session, confirmation):
    assignment = confirmation.assignment
    for resource in (
        ("WEAPON_TOMESTONE", "WEAPON_AUGMENT")
        if _is_paired_tome(assignment)
        else (assignment.loot_key,)
    ):
        _balance(session, assignment.plan_id, confirmation.recipient_id, resource, 1)
    effects = list(
        session.scalars(
            select(V2EffectLedger)
            .where(V2EffectLedger.confirmation_id == confirmation.id)
            .order_by(V2EffectLedger.id)
        )
    )
    latest = {effect.slot_key: effect for effect in effects}
    for effect in latest.values():
        slot = session.scalar(select(GearSlot).where(GearSlot.code == effect.slot_key))
        current = session.scalar(
            select(CharacterGearSlot).where(
                CharacterGearSlot.character_id == effect.recipient_id,
                CharacterGearSlot.gear_slot_id == slot.id,
            )
        )
        if current is None or current.current_classification.value != effect.after_category:
            raise V2ConfirmationError(
                f"Gear slot {effect.slot_key} changed after V2 application; resolve manually."
            )
        if effect.before_category is None:
            session.delete(current)
        else:
            current.current_classification = GearClassification(effect.before_category)


def _correction_state(row):
    return V2CorrectionState(
        row.id,
        row.confirmation_id,
        row.correction_type,
        row.corrected_success,
        row.actor_id,
        row.reason,
        row.created_at,
    )


def _balance(session, plan_id, recipient_id, resource_key, delta):
    plan = session.get(V2Plan, plan_id)
    if plan is None:
        raise V2ConfirmationError("The V2 plan was not found.")
    try:
        return adjust_current_balance(session, plan.static_id, recipient_id, resource_key, delta)
    except ValueError as exc:
        raise V2ConfirmationError(str(exc)) from exc


def _record_application(session, assignment, recipient, success, actor_id, note):
    row = V2Confirmation(
        assignment_id=assignment.id,
        resource_key="APPLICATION",
        action="APPLICATION",
        success=success,
        recipient_id=recipient,
        quantity=1,
        actor_id=actor_id,
        note=note,
    )
    session.add(row)
    session.flush()
    return row


def _apply_assignment(session, assignment, recipient, confirmation):
    resources = (
        ("WEAPON_TOMESTONE", "WEAPON_AUGMENT")
        if _is_paired_tome(assignment)
        else (assignment.loot_key,)
    )
    for resource in resources:
        balance = session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.static_id == assignment.run.plan.static_id,
                V2ResourceBalance.recipient_id == recipient,
                V2ResourceBalance.resource_key == validate_resource_key(resource),
            )
        )
        if balance is None or balance.quantity < 1:
            raise V2ConfirmationError(f"No received {resource} resource is available to apply.")
        balance.quantity -= 1
    for effect in sorted(assignment.effects, key=lambda row: row.sort_order):
        slot = session.scalar(select(GearSlot).where(GearSlot.code == effect.slot_key))
        current = session.scalar(
            select(CharacterGearSlot).where(
                CharacterGearSlot.character_id == recipient,
                CharacterGearSlot.gear_slot_id == slot.id,
            )
        )
        before = current.current_classification if current else None
        if current is None:
            current = CharacterGearSlot(
                character_id=recipient,
                gear_slot_id=slot.id,
                current_classification=GearClassification(effect.resulting_category),
            )
            session.add(current)
        else:
            current.current_classification = GearClassification(effect.resulting_category)
        session.add(
            V2EffectLedger(
                confirmation_id=confirmation.id,
                recipient_id=recipient,
                slot_key=effect.slot_key,
                resulting_category=effect.resulting_category,
                before_category=before.value if before else None,
                after_category=effect.resulting_category,
                quantity_delta=1,
            )
        )


def _state(row):
    return V2ConfirmationState(
        row.id,
        row.assignment_id,
        row.resource_key,
        row.action,
        row.success,
        row.recipient_id,
        row.quantity,
        row.actor_id,
        row.created_at,
        row.note,
    )
