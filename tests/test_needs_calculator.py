"""Pure tests for the side-by-side needs calculator."""

from dataclasses import fields

import pytest

from app.models import GearClassification, GearSlotCode, LootCategory
from app.schemas.needs_v2 import NeedsV2Status
from app.services.needs_calculator import calculate_needs_from_state
from app.services.needs_state import (
    CharacterNeedsState,
    NeedsFloorRuleState,
    NeedsFloorState,
    NeedsInventoryState,
    NeedsLootTypeState,
    NeedsMaterialTypeState,
    NeedsSlotState,
)


def make_state(slots, *, books=(), materials=(), inventory=(), uses_offhand=True, warnings=()):
    loot = NeedsLootTypeState(10, "DROP", "Drop", LootCategory.GEAR)
    coffer = NeedsLootTypeState(11, "COFFER", "Coffer", LootCategory.COFFER)
    rules = (
        NeedsFloorRuleState(10, "DROP", "Drop", LootCategory.GEAR, 1, None),
        NeedsFloorRuleState(11, "COFFER", "Coffer", LootCategory.COFFER, 1, None),
    )
    floor = NeedsFloorState(100, 1, "Floor", rules)
    return CharacterNeedsState(
        1,
        "Character",
        2,
        "Static",
        3,
        "JOB",
        uses_offhand,
        4,
        "BiS",
        tuple(slots),
        (floor,),
        (loot, coffer),
        (NeedsMaterialTypeState(20, "MAT", "Material"),),
        tuple(books),
        tuple(materials),
        tuple(inventory),
        tuple(warnings),
    )


def slot(code, desired, current=None, order=1, **kwargs):
    return NeedsSlotState(
        100 + order, code, code.value.title(), order, desired, current, False, **kwargs
    )


def one(state):
    return calculate_needs_from_state(state).slot_results[0]


@pytest.mark.parametrize(
    ("current", "desired", "status"),
    [
        (GearClassification.TOME, GearClassification.TOME, NeedsV2Status.COMPLETE),
        (GearClassification.SAVAGE, GearClassification.TOME, NeedsV2Status.INVALID_CONFIGURATION),
        (
            GearClassification.CRAFTED_EX,
            GearClassification.TOME,
            NeedsV2Status.INVALID_CONFIGURATION,
        ),
        (GearClassification.GARBAGE, GearClassification.TOME, NeedsV2Status.INVALID_CONFIGURATION),
    ],
)
def test_exact_category_completion_and_invalid_requirements(current, desired, status):
    result = one(
        make_state(
            (
                slot(
                    GearSlotCode.HEAD,
                    desired,
                    current,
                    required_floor_number=1,
                    required_loot_type_id=10,
                    required_loot_type_code="DROP",
                ),
            )
        )
    )
    assert result.status is status


def test_manual_and_not_applicable_completion():
    manual = NeedsSlotState(1, GearSlotCode.HEAD, "Head", 1, GearClassification.TOME, None, True)
    not_applicable = slot(GearSlotCode.OFFHAND, GearClassification.NOT_APPLICABLE, order=2)
    result = calculate_needs_from_state(make_state((manual, not_applicable)))
    assert [row.status for row in result.slot_results] == [
        NeedsV2Status.MANUALLY_COMPLETE,
        NeedsV2Status.NOT_APPLICABLE,
    ]


def test_savage_coffer_is_allocated_once_and_grouped():
    slots = tuple(
        (
            slot(
                code,
                GearClassification.SAVAGE,
                order=i,
                required_floor_number=1,
                required_loot_type_id=11,
                required_loot_type_code="COFFER",
            )
            for i, code in enumerate((GearSlotCode.HEAD, GearSlotCode.BODY), 1)
        )
    )
    inventory = (NeedsInventoryState(1, None, None, 11, 1),)
    result = calculate_needs_from_state(make_state(slots, inventory=inventory))
    assert [row.coffer_allocated for row in result.slot_results] == [True, False]
    assert result.slot_results[1].status is NeedsV2Status.NEEDS_SAVAGE_DROP
    assert result.coffer_summaries[0].owned == 1
    assert result.savage_needs[0].quantity == 2


def test_augmented_tome_base_inventory_and_material_allocation():
    slots = tuple(
        (
            slot(
                code,
                GearClassification.AUGMENTED_TOME,
                order=i,
                required_floor_number=1,
                required_loot_type_id=10,
                required_loot_type_code="DROP",
                required_material_type_id=20,
            )
            for i, code in enumerate((GearSlotCode.HEAD, GearSlotCode.BODY, GearSlotCode.HANDS), 1)
        )
    )
    inventory = (
        NeedsInventoryState(1, 101, GearClassification.TOME, None, 1),
        NeedsInventoryState(2, 102, GearClassification.TOME, None, 1),
    )
    result = calculate_needs_from_state(
        make_state(slots, materials=((20, 1),), inventory=inventory)
    )
    assert [row.status for row in result.slot_results] == [
        NeedsV2Status.READY_TO_AUGMENT,
        NeedsV2Status.NEEDS_AUGMENTATION,
        NeedsV2Status.NEEDS_BASE_TOME,
    ]
    assert result.material_needs[0].total_required == 3
    assert result.material_needs[0].allocated == 1
    assert result.material_needs[0].additional_needed == 2


def test_books_are_informational_and_do_not_change_savage_needs():
    slots = tuple(
        (
            slot(
                code,
                GearClassification.SAVAGE,
                order=i,
                required_floor_number=1,
                required_loot_type_id=10,
                required_loot_type_code="DROP",
            )
            for i, code in enumerate((GearSlotCode.HEAD, GearSlotCode.BODY), 1)
        )
    )
    without_books = calculate_needs_from_state(make_state(slots))
    with_books = calculate_needs_from_state(make_state(slots, books=((1, 999),)))
    assert [row.status for row in with_books.slot_results] == [
        row.status for row in without_books.slot_results
    ]
    assert [(row.floor_number, row.available) for row in with_books.book_balances] == [(1, 999)]


def test_offhand_warning_and_missing_configuration():
    result = one(
        make_state(
            (
                slot(
                    GearSlotCode.OFFHAND,
                    GearClassification.SAVAGE,
                    required_floor_number=1,
                    required_loot_type_id=10,
                    required_loot_type_code="DROP",
                ),
            ),
            uses_offhand=False,
        )
    )
    assert result.status is NeedsV2Status.INVALID_CONFIGURATION
    assert result.warnings


def test_result_is_immutable_ordered_and_has_no_tier_fields():
    state = make_state(
        (
            slot(GearSlotCode.BODY, GearClassification.NOT_APPLICABLE, order=2),
            slot(GearSlotCode.HEAD, GearClassification.NOT_APPLICABLE, order=1),
        )
    )
    result = calculate_needs_from_state(state)
    assert [row.gear_slot for row in result.slot_results] == [GearSlotCode.HEAD, GearSlotCode.BODY]
    assert all("tier" not in field.name.lower() for field in fields(result))
    with pytest.raises((AttributeError, TypeError)):
        result.slot_results += ()
    assert not any(hasattr(value, "__table__") for value in result.slot_results)
