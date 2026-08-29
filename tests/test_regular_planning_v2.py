"""Detailed behavioral coverage for the pure Regular V2 planner."""

import pytest

from app.models import CharacterKind, ClearMode, GearSlotCode
from app.services.regular_planning_v2 import generate_regular_plan_v2
from tests.v2_test_helpers import character, state


def _proposal(**kwargs):
    return generate_regular_plan_v2(state(**kwargs))


def test_regular_proposal_contains_fixed_floor_resources():
    proposal = _proposal()
    assert {row.floor_number for row in proposal.assignments + proposal.unassigned} == {1, 2, 3, 4}
    assert proposal.mode is ClearMode.REGULAR


def test_regular_assignment_order_is_deterministic():
    assert _proposal() == _proposal()


def test_highest_hierarchy_wins_useful_savage_coffer():
    low = character(1, 1, CharacterKind.MAIN, position=1)
    high = character(2, 2, CharacterKind.MAIN, position=2)
    result = _proposal(
        mains=(low, high, *[character(i, i, CharacterKind.MAIN, position=i) for i in range(3, 9)])
    )
    row = next(row for row in result.assignments if row.loot_type == "ACCESSORY_COFFER")
    assert row.recipient_id == 1


def test_one_main_can_receive_multiple_coffers():
    result = _proposal()
    assigned = [row.recipient_id for row in result.assignments if row.primary_slot is not None]
    assert len(assigned) > len(set(assigned))


def test_assignment_count_does_not_change_priority():
    result = _proposal()
    assert result.assignments[0].score.assignments_in_proposal == 0


@pytest.mark.parametrize("material", ["ACCESSORY_GLAZE", "ARMOR_TWINE"])
def test_materials_are_main_only(material):
    mains = tuple(
        character(i, i, CharacterKind.MAIN, material=True, position=i) for i in range(1, 9)
    )
    alts = tuple(
        character(i + 8, i, CharacterKind.ALT, material=True, position=i) for i in range(1, 9)
    )
    result = _proposal(mains=mains, alts=alts)
    assert all(
        row.recipient_kind.value == "MAIN"
        for row in result.assignments
        if row.material_type == material
    )


@pytest.mark.parametrize("material", ["ACCESSORY_GLAZE", "ARMOR_TWINE"])
def test_material_without_main_need_is_free_for_all(material):
    mains = tuple(character(i, i, CharacterKind.MAIN, savage=True, position=i) for i in range(1, 9))
    result = _proposal(mains=mains)
    row = next(row for row in result.unassigned if row.material_type == material)
    assert "No eligible" in row.reason


def test_weapon_coffer_has_weapon_and_applicable_offhand_effects():
    main = character(1, 1, CharacterKind.MAIN, offhand=True, position=1)
    result = _proposal(
        mains=(main, *[character(i, i, CharacterKind.MAIN, position=i) for i in range(2, 9)])
    )
    row = next(row for row in result.assignments if row.loot_type == "WEAPON_COFFER")
    assert [effect.slot_key for effect in row.gear_effects] == [
        GearSlotCode.WEAPON,
        GearSlotCode.OFFHAND,
    ]


def test_non_offhand_job_gets_only_weapon_effect():
    result = _proposal()
    row = next(row for row in result.assignments if row.loot_type == "WEAPON_COFFER")
    assert [effect.slot_key for effect in row.gear_effects] == [GearSlotCode.WEAPON]


def test_unneeded_resources_are_unassigned():
    result = _proposal(
        mains=tuple(
            character(i, i, CharacterKind.MAIN, savage=False, position=i) for i in range(1, 9)
        )
    )
    assert result.unassigned


def test_books_and_tier_configuration_do_not_enter_proposal():
    result = _proposal()
    assert all("BOOK" not in row.loot_type for row in result.unassigned)
    assert all("tier" not in row.reason.lower() for row in result.unassigned)


def test_regular_planner_is_pure_and_identical_state_is_equal():
    before = state()
    first = generate_regular_plan_v2(before)
    second = generate_regular_plan_v2(before)
    assert first == second
    assert first.fingerprint == second.fingerprint
