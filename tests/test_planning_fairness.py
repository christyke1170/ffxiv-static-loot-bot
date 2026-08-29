"""Effective V2 fairness and fingerprint regression cases."""

from types import SimpleNamespace
from unittest.mock import Mock

from app.schemas.planning_state import PlanningFairness
from app.services.planning_state import _fairness
from app.services.v2_plan_state_fingerprint import planning_state_fingerprint
from tests.v2_test_helpers import state


def _fake_session(rows=(), corrections=()):
    class Result(list):
        def all(self):
            return self

    session = Mock()
    session.scalars.side_effect = [Result(rows), Result(corrections)]
    return session


def test_savage_receipts_enter_effective_history(session):
    row = SimpleNamespace(
        id=1, resource_key="HEAD_COFFER", action="RECEIPT", success=True, recipient_id=1, quantity=2
    )
    assert _fairness(_fake_session((row,)), (1,))[0].savage_receipts == 2


def test_glaze_and_twine_are_distinct_categories():
    fairness = PlanningFairness(1, 0, (("ACCESSORY_GLAZE", 1), ("ARMOR_TWINE", 2)))
    assert dict(fairness.material_grants) == {"ACCESSORY_GLAZE": 1, "ARMOR_TWINE": 2}


def test_identical_confirmation_retry_counts_once(session):
    row = SimpleNamespace(
        id=1, resource_key="HEAD_COFFER", action="RECEIPT", success=True, recipient_id=1, quantity=1
    )
    assert _fairness(_fake_session((row,)), (1,))[0].savage_receipts == 1


def test_successful_to_failed_correction_removes_fairness(session):
    confirmation = SimpleNamespace(
        id=1, resource_key="HEAD_COFFER", action="RECEIPT", success=True, recipient_id=1, quantity=1
    )
    correction = SimpleNamespace(confirmation_id=1, corrected_success=False)
    assert _fairness(_fake_session((confirmation,), (correction,)), (1,))[0].savage_receipts == 0


def test_failed_to_successful_correction_restores_fairness_once(session):
    confirmation = SimpleNamespace(
        id=1,
        resource_key="HEAD_COFFER",
        action="RECEIPT",
        success=False,
        recipient_id=1,
        quantity=1,
    )
    correction = SimpleNamespace(confirmation_id=1, corrected_success=True)
    assert _fairness(_fake_session((confirmation,), (correction,)), (1,))[0].savage_receipts == 1


def test_application_rows_do_not_enter_receipt_fairness(session):
    row = SimpleNamespace(
        id=1,
        resource_key="APPLICATION",
        action="APPLICATION",
        success=True,
        recipient_id=1,
        quantity=1,
    )
    assert _fairness(_fake_session((row,)), (1,))[0].savage_receipts == 0


def test_static_lifetime_fairness_state_is_not_tier_scoped():
    value = state()
    assert planning_state_fingerprint(value) == planning_state_fingerprint(value)


def test_fingerprint_changes_when_effective_fairness_changes():
    first = state()
    second = state(fairness=(PlanningFairness(1, 1, ()),))
    assert planning_state_fingerprint(first) != planning_state_fingerprint(second)


def test_failed_receipts_do_not_enter_effective_fairness():
    row = SimpleNamespace(
        id=2,
        resource_key="HEAD_COFFER",
        action="RECEIPT",
        success=False,
        recipient_id=1,
        quantity=1,
    )
    assert _fairness(_fake_session((row,)), (1,))[0].savage_receipts == 0


def test_material_fairness_is_quantity_based_and_static_scoped():
    row = SimpleNamespace(
        id=3, resource_key="ARMOR_TWINE", action="RECEIPT", success=True, recipient_id=1, quantity=2
    )
    fairness = _fairness(_fake_session((row,)), (1,))[0]
    assert dict(fairness.material_grants) == {"ARMOR_TWINE": 2}
