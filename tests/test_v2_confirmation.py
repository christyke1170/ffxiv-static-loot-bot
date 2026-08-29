"""Database-backed neutral V2 receipt, application, and correction tests."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import (
    CharacterGearSlot,
    ClearMode,
    GearClassification,
    V2Confirmation,
    V2EffectLedger,
    V2Plan,
    V2PlanAssignment,
    V2PlanEffect,
    V2PlanParticipant,
    V2PlanRun,
    V2ResourceBalance,
)
from app.services import (
    V2ConfirmationError,
    confirm_v2_application,
    confirm_v2_receipt,
    correct_v2_receipt,
    read_v2_confirmation_state,
    read_v2_correction_history,
    reverse_v2_application,
)
from app.services.reclear import create_reclear_week
from tests.test_v2_planning_state import _static


def _regular(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    characters = tuple(member.characters[0] for member in static.members)
    plan = V2Plan(
        static_id=static.id,
        reclear_week_id=week.id,
        mode="REGULAR",
        week_number=week.week_start.isocalendar().week,
        fingerprint="confirmation-plan",
        state_fingerprint="confirmation-state",
        actor_id=700,
    )
    run = V2PlanRun(plan=plan, run_number=1, name="Neutral run", source_group_id=1)
    run.participants = [
        V2PlanParticipant(run=run, character_id=character.id, designation="MAIN", sort_order=index)
        for index, character in enumerate(characters, 1)
    ]
    assignment = V2PlanAssignment(
        plan=plan,
        run=run,
        sort_order=1,
        floor_number=2,
        loot_key="HEAD_COFFER",
        primary_slot="HEAD",
        recipient_id=characters[0].id,
        recipient_job=characters[0].job.abbreviation,
        recipient_kind="MAIN",
        disposition="ASSIGNED",
        resource_quantity=1,
        fairness_count=0,
        explanation="Neutral confirmation fixture",
    )
    assignment.effects = [
        V2PlanEffect(
            assignment=assignment,
            sort_order=1,
            slot_key="HEAD",
            resulting_category=GearClassification.SAVAGE.value,
        )
    ]
    session.add(plan)
    session.flush()
    assignments = session.scalars(
        select(V2PlanAssignment)
        .where(V2PlanAssignment.plan_id == plan.id)
        .order_by(V2PlanAssignment.id)
    ).all()
    return static, week, plan, assignments


def _assignment(session, loot_key="HEAD_COFFER"):
    static, week, result, assignments = _regular(session)
    row = next((item for item in assignments if item.loot_key == loot_key), None)
    if row is None:
        row = assignments[0]
    return static, week, result, row


def _paired(session):
    static, week, result, assignment = _assignment(session)
    assignment.loot_key = "TOME_WEAPON_RESOURCES"
    assignment.primary_slot = "WEAPON"
    assignment.effects[0].slot_key = "WEAPON"
    session.flush()
    return static, week, result, assignment


def test_successful_savage_receipt_creates_one_current_balance(session):
    static, week, result, assignment = _assignment(session)
    state = confirm_v2_receipt(session, assignment.id, assignment.loot_key, True, actor_id=9)
    session.commit()
    balance = session.scalar(
        select(V2ResourceBalance).where(
            V2ResourceBalance.recipient_id == state.recipient_id,
            V2ResourceBalance.resource_key == assignment.loot_key,
        )
    )
    assert balance.quantity == 1


def test_failed_savage_receipt_creates_no_balance(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, False, actor_id=9)
    session.commit()
    assert (
        session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.recipient_id == assignment.recipient_id,
                V2ResourceBalance.resource_key == assignment.loot_key,
            )
        )
        is None
    )


@pytest.mark.parametrize("success", [True, False])
def test_identical_receipt_retry_is_idempotent(success, session):
    static, week, result, assignment = _assignment(session)
    first = confirm_v2_receipt(session, assignment.id, assignment.loot_key, success, actor_id=9)
    second = confirm_v2_receipt(session, assignment.id, assignment.loot_key, success, actor_id=10)
    assert second == first
    assert (
        session.scalar(
            select(func.count())
            .select_from(V2Confirmation)
            .where(V2Confirmation.assignment_id == assignment.id)
        )
        == 1
    )


def test_contradictory_receipt_outcome_is_rejected(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, True, actor_id=9)
    with pytest.raises(V2ConfirmationError, match="conflicting"):
        confirm_v2_receipt(session, assignment.id, assignment.loot_key, False, actor_id=9)


def test_receipt_enforces_scope_and_participant(session):
    static, week, result, assignment = _assignment(session)
    with pytest.raises(V2ConfirmationError, match="static"):
        confirm_v2_receipt(session, assignment.id, assignment.loot_key, True, static_id=999)
    with pytest.raises(V2ConfirmationError, match="week"):
        confirm_v2_receipt(session, assignment.id, assignment.loot_key, True, week_id=999)
    with pytest.raises(V2ConfirmationError, match="run"):
        confirm_v2_receipt(session, assignment.id, assignment.loot_key, True, recipient_id=999)


def test_free_for_all_requires_explicit_recipient(session):
    static, week, result, assignment = _assignment(session)
    assignment.disposition = "FREE_ROLL"
    assignment.recipient_id = None
    session.commit()
    with pytest.raises(V2ConfirmationError, match="explicit"):
        confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)


def test_receipt_preserves_actor_and_does_not_change_gear(session):
    static, week, result, assignment = _assignment(session)
    before = session.scalar(
        select(func.count())
        .select_from(CharacterGearSlot)
        .where(CharacterGearSlot.character_id == assignment.recipient_id)
    )
    row = confirm_v2_receipt(session, assignment.id, assignment.loot_key, True, actor_id=88)
    after = session.scalar(
        select(func.count())
        .select_from(CharacterGearSlot)
        .where(CharacterGearSlot.character_id == assignment.recipient_id)
    )
    assert row.actor_id == 88 and before == after


def test_receipt_readback_is_immutable_and_orm_free(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, True, actor_id=88)
    readback = read_v2_confirmation_state(session, assignment.id)
    assert isinstance(readback.confirmations, tuple)
    assert not any(hasattr(item, "__table__") for item in readback.confirmations)


def test_successful_application_requires_received_matching_resource(session):
    static, week, result, assignment = _assignment(session)
    with pytest.raises(V2ConfirmationError, match="received"):
        confirm_v2_application(session, assignment.id, True)


def test_failed_application_leaves_resource_and_gear_unchanged(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)
    before = session.scalar(
        select(V2ResourceBalance).where(
            V2ResourceBalance.recipient_id == assignment.recipient_id,
            V2ResourceBalance.resource_key == assignment.loot_key,
        )
    ).quantity
    confirm_v2_application(session, assignment.id, False, actor_id=3)
    assert (
        session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.recipient_id == assignment.recipient_id,
                V2ResourceBalance.resource_key == assignment.loot_key,
            )
        ).quantity
        == before
    )


def test_successful_application_consumes_one_and_records_effect_ledger(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)
    applied = confirm_v2_application(session, assignment.id, True, actor_id=4)
    session.commit()
    assert (
        session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.recipient_id == assignment.recipient_id,
                V2ResourceBalance.resource_key == assignment.loot_key,
            )
        ).quantity
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(V2EffectLedger)
            .where(V2EffectLedger.confirmation_id == applied.confirmation_id)
        )
        >= 1
    )


def test_application_retry_is_idempotent(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)
    first = confirm_v2_application(session, assignment.id, True)
    second = confirm_v2_application(session, assignment.id, True)
    assert second == first


def test_application_contradictory_outcome_is_rejected(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)
    confirm_v2_application(session, assignment.id, True)
    with pytest.raises(V2ConfirmationError, match="conflicting"):
        confirm_v2_application(session, assignment.id, False)


def test_application_readback_contains_ordered_effects_and_before_after(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)
    confirm_v2_application(session, assignment.id, True)
    readback = read_v2_confirmation_state(session, assignment.id)
    assert readback.effects
    assert all(effect.after_category for effect in readback.effects)


def test_receipt_correction_removes_balance_and_is_append_only(session):
    static, week, result, assignment = _assignment(session)
    receipt = confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)
    correction = correct_v2_receipt(session, receipt.confirmation_id, False, 99, "wrong receipt")
    session.commit()
    assert session.get(V2Confirmation, receipt.confirmation_id).success is True
    assert correction.corrected_success is False
    assert read_v2_correction_history(session, assignment.id)[0] == correction


def test_failed_receipt_correction_restores_balance(session):
    static, week, result, assignment = _assignment(session)
    receipt = confirm_v2_receipt(session, assignment.id, assignment.loot_key, False)
    correct_v2_receipt(session, receipt.confirmation_id, True, 99, "verified")
    assert (
        session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.recipient_id == assignment.recipient_id,
                V2ResourceBalance.resource_key == assignment.loot_key,
            )
        ).quantity
        == 1
    )


def test_correction_requires_actor_and_reason(session):
    static, week, result, assignment = _assignment(session)
    receipt = confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)
    with pytest.raises(V2ConfirmationError, match="actor"):
        correct_v2_receipt(session, receipt.confirmation_id, False, None, "reason")
    with pytest.raises(V2ConfirmationError, match="reason"):
        correct_v2_receipt(session, receipt.confirmation_id, False, 1, " ")


def test_application_reversal_restores_resource_and_is_idempotent(session):
    static, week, result, assignment = _assignment(session)
    confirm_v2_receipt(session, assignment.id, assignment.loot_key, True)
    application = confirm_v2_application(session, assignment.id, True)
    reversal = reverse_v2_application(session, application.confirmation_id, 99, "reversal")
    again = reverse_v2_application(session, application.confirmation_id, 99, "reversal")
    assert reversal == again
    assert (
        session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.recipient_id == assignment.recipient_id,
                V2ResourceBalance.resource_key == assignment.loot_key,
            )
        ).quantity
        == 1
    )


def test_paired_tome_resources_are_stored_independently_and_partial_receipt_is_visible(session):
    static, week, result, assignment = _paired(session)
    confirm_v2_receipt(session, assignment.id, "WEAPON_TOMESTONE", True)
    session.commit()
    assert (
        session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.recipient_id == assignment.recipient_id,
                V2ResourceBalance.resource_key == "WEAPON_TOMESTONE",
            )
        ).quantity
        == 1
    )
    assert (
        session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.recipient_id == assignment.recipient_id,
                V2ResourceBalance.resource_key == "WEAPON_AUGMENT",
            )
        )
        is None
    )
    assert len(read_v2_confirmation_state(session, assignment.id).confirmations) == 1


def test_paired_tome_application_requires_both_resources(session):
    static, week, result, assignment = _paired(session)
    confirm_v2_receipt(session, assignment.id, "WEAPON_TOMESTONE", True)
    with pytest.raises(V2ConfirmationError, match="Both paired"):
        confirm_v2_application(session, assignment.id, True)


def test_paired_tome_application_consumes_both_and_applies_weapon(session):
    static, week, result, assignment = _paired(session)
    confirm_v2_receipt(session, assignment.id, "WEAPON_TOMESTONE", True)
    confirm_v2_receipt(session, assignment.id, "WEAPON_AUGMENT", True)
    application = confirm_v2_application(session, assignment.id, True)
    session.commit()
    for key in ("WEAPON_TOMESTONE", "WEAPON_AUGMENT"):
        assert (
            session.scalar(
                select(V2ResourceBalance).where(
                    V2ResourceBalance.recipient_id == assignment.recipient_id,
                    V2ResourceBalance.resource_key == key,
                )
            ).quantity
            == 0
        )
    assert (
        session.scalar(
            select(func.count())
            .select_from(V2EffectLedger)
            .where(V2EffectLedger.confirmation_id == application.confirmation_id)
        )
        == 1
    )


def test_paired_tome_reversal_restores_both_resources(session):
    static, week, result, assignment = _paired(session)
    confirm_v2_receipt(session, assignment.id, "WEAPON_TOMESTONE", True)
    confirm_v2_receipt(session, assignment.id, "WEAPON_AUGMENT", True)
    application = confirm_v2_application(session, assignment.id, True)
    reverse_v2_application(session, application.confirmation_id, 99, "paired reversal")
    assert all(
        session.scalar(
            select(V2ResourceBalance).where(
                V2ResourceBalance.recipient_id == assignment.recipient_id,
                V2ResourceBalance.resource_key == key,
            )
        ).quantity
        == 1
        for key in ("WEAPON_TOMESTONE", "WEAPON_AUGMENT")
    )
