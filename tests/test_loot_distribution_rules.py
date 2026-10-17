"""Owner-confirmed greedy distribution examples and recipient restrictions."""

from dataclasses import replace

import pytest

from app.models import CharacterKind
from app.models import GearClassification as Category
from app.models import GearSlotCode as Slot
from app.schemas.needs_v2 import NeedsV2Status as Status
from app.schemas.planning_state import PlanningFairness
from app.services.regular_planning_v2 import generate_regular_plan_v2
from app.services.split_planning_v2 import generate_split_plan_v2
from tests.v2_test_helpers import character, split_state, state


def with_needs(person, slots):
    rows = tuple(
        replace(
            row,
            desired=Category.SAVAGE,
            current=Category.CRAFTED_EX if row.gear_slot in slots else Category.SAVAGE,
            status=Status.NEEDS_SAVAGE_DROP if row.gear_slot in slots else Status.COMPLETE,
        )
        for row in person.needs.slot_results
    )
    return replace(person, needs=replace(person.needs, slot_results=rows))


def dps_example():
    original = split_state()
    targets = {
        5: {Slot.RING_1, Slot.EARRINGS, Slot.HANDS, Slot.BODY, Slot.FEET},
        6: {Slot.RING_1, Slot.BRACELETS, Slot.EARRINGS, Slot.HEAD, Slot.LEGS},
        7: {Slot.RING_1, Slot.EARRINGS, Slot.NECKLACE, Slot.BODY, Slot.HANDS, Slot.FEET},
        8: {Slot.RING_1, Slot.BRACELETS, Slot.EARRINGS, Slot.HANDS, Slot.LEGS},
    }
    names = {5: "SAM", 6: "DRG", 7: "PCT", 8: "DNC"}
    mains = tuple(
        replace(
            with_needs(row, targets.get(row.character_id, set())),
            hierarchy_position=row.character_id - 4
            if row.character_id >= 5
            else row.character_id + 4,
            job_abbreviation=names.get(row.character_id, row.job_abbreviation),
        )
        for row in original.mains
    )
    positions = {row.job_id: row.hierarchy_position for row in mains}
    alts = tuple(
        replace(with_needs(row, set()), hierarchy_position=positions[row.job_id])
        for row in original.alts
    )
    return replace(
        original,
        mains=mains,
        alts=alts,
        hierarchy=tuple(
            (row.job_id, row.job_abbreviation, row.hierarchy_position) for row in mains
        ),
    )


def main_counts(proposal):
    return {
        identifier: sum(
            row.recipient_id == identifier
            and bool(row.gear_effects)
            and row.loot_key.endswith("COFFER")
            for group in proposal.groups
            for row in group.assignments
        )
        for identifier in range(1, 9)
    }


def test_requested_dps_pairing_frontloads_sam_and_drg_and_covers_16_needs():
    proposal = generate_split_plan_v2(dps_example())
    assert main_counts(proposal) == {1: 0, 2: 0, 3: 0, 4: 0, 5: 5, 6: 5, 7: 4, 8: 2}
    sam_run = next(group for group in proposal.groups if 5 in group.participant_ids)
    assert 8 in sam_run.participant_ids and 6 not in sam_run.participant_ids
    for group in proposal.groups:
        assert sum(identifier <= 8 for identifier in group.participant_ids) == 4
        assert sum(identifier > 8 for identifier in group.participant_ids) == 4


def test_next_week_uses_current_gear_and_finishes_remaining_dps_needs():
    original = dps_example()
    first = generate_split_plan_v2(original)
    effects = {
        (row.recipient_id, effect.slot_key)
        for group in first.groups
        for row in group.assignments
        for effect in row.gear_effects
    }
    mains = tuple(
        replace(
            person,
            needs=replace(
                person.needs,
                slot_results=tuple(
                    replace(row, current=Category.SAVAGE, status=Status.COMPLETE)
                    if (person.character_id, row.gear_slot) in effects
                    else row
                    for row in person.needs.slot_results
                ),
            ),
        )
        for person in original.mains
    )
    second = generate_split_plan_v2(replace(original, mains=mains))
    assert main_counts(second) == {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 2, 8: 3}


def test_greedy_savage_ignores_history_and_assignment_count():
    high = character(1, 1, CharacterKind.MAIN, position=1)
    low = character(2, 2, CharacterKind.MAIN, position=2)
    proposal = generate_regular_plan_v2(
        state(mains=(high, low), fairness=(PlanningFairness(1, 99, ()),))
    )
    assert {row.recipient_id for row in proposal.assignments if row.gear_effects} == {1}


def test_regular_valid_savage_slot_survives_unrelated_configuration_warning():
    main = character(1, 1, CharacterKind.MAIN, position=1)
    main = replace(main, needs=replace(main.needs, configuration_warnings=("Unrelated Tome slot",)))
    proposal = generate_regular_plan_v2(state(mains=(main,)))
    assert any(
        row.loot_type == "HEAD_COFFER" and row.recipient_id == 1 for row in proposal.assignments
    )
    assert proposal.warnings


def test_split_unused_materials_and_weapon_resources_do_not_become_unrestricted_assignments():
    original = split_state()
    alts = tuple(with_needs(row, set()) for row in original.alts)
    proposal = generate_split_plan_v2(replace(original, alts=alts))
    restricted = {"ACCESSORY_GLAZE", "ARMOR_TWINE", "WEAPON_TOMESTONE", "WEAPON_AUGMENT"}
    assert not any(
        row.loot_key in restricted for group in proposal.groups for row in group.assignments
    )
    assert restricted <= {row.loot_key for row in proposal.unassigned}


def test_manual_random_weapon_removes_next_week_coffer_need_without_a_random_drop():
    main = with_needs(character(1, 1, CharacterKind.MAIN, position=1), {Slot.WEAPON})
    first = generate_regular_plan_v2(state(mains=(main,)))
    assert any(row.loot_type == "WEAPON_COFFER" for row in first.assignments)
    updated = with_needs(main, set())
    second = generate_regular_plan_v2(state(mains=(updated,)))
    assert not any(row.loot_type == "WEAPON_COFFER" for row in second.assignments)
    assert all("RANDOM" not in row.loot_type for row in second.assignments + second.unassigned)


def material_main(identifier, *, need=2, position=None):
    person = character(
        identifier, identifier, CharacterKind.MAIN, material=True, position=position or identifier
    )
    materials = tuple(replace(row, additional_needed=need) for row in person.needs.material_needs)
    return replace(person, needs=replace(person.needs, material_needs=materials))


@pytest.mark.parametrize("material", ["ACCESSORY_GLAZE", "ARMOR_TWINE"])
def test_regular_materials_use_combined_fairness_before_need_and_hierarchy(material):
    high, low = material_main(1, need=4), material_main(2, need=2)
    proposal = generate_regular_plan_v2(
        state(mains=(high, low), fairness=(PlanningFairness(1, 0, (("ACCESSORY_GLAZE", 5),)),))
    )
    grant = next(row for row in proposal.assignments if row.material_type == material)
    assert grant.recipient_id == 2


def test_regular_materials_count_earlier_planned_grants_for_fairness():
    proposal = generate_regular_plan_v2(state(mains=(material_main(1), material_main(2))))
    grants = {
        row.material_type: row.recipient_id for row in proposal.assignments if row.material_type
    }
    assert grants == {"ACCESSORY_GLAZE": 1, "ARMOR_TWINE": 2}


def test_twine_equal_fairness_prefers_more_need_before_hierarchy():
    # No Glaze need, so only Twine determines the fair allocation.
    mains = tuple(
        replace(
            person,
            needs=replace(
                person.needs,
                material_needs=tuple(
                    row for row in person.needs.material_needs if row.material_code == "ARMOR_TWINE"
                ),
            ),
        )
        for person in (material_main(1, need=2), material_main(2, need=4))
    )
    proposal = generate_regular_plan_v2(state(mains=mains))
    assert (
        next(row.recipient_id for row in proposal.assignments if row.material_type == "ARMOR_TWINE")
        == 2
    )


def test_three_dps_mains_in_one_run_is_allowed_when_it_gears_more_priority_needs():
    original = dps_example()
    targets = {5: {Slot.HEAD}, 6: {Slot.BODY}, 7: {Slot.LEGS}, 8: {Slot.HEAD, Slot.BODY, Slot.LEGS}}
    mains = tuple(
        with_needs(person, targets.get(person.character_id, set())) for person in original.mains
    )
    proposal = generate_split_plan_v2(replace(original, mains=mains))
    assert [main_counts(proposal)[identifier] for identifier in (5, 6, 7, 8)] == [1, 1, 1, 3]
    assert sorted(
        sum(identifier in {5, 6, 7, 8} for identifier in group.participant_ids)
        for group in proposal.groups
    ) == [1, 3]


def test_tank_main_beats_any_dps_alt_for_a_contested_bis_coffer():
    original = split_state()
    mains = tuple(
        with_needs(person, {Slot.HEAD} if person.combat_role == "TANK" else set())
        for person in original.mains
    )
    proposal = generate_split_plan_v2(replace(original, mains=mains))
    heads = [
        row
        for group in proposal.groups
        for row in group.assignments
        if row.loot_key == "HEAD_COFFER"
    ]
    assert len(heads) == 2
    assert {row.recipient_id for row in heads} == {1, 2}
    assert all(row.recipient_kind is CharacterKind.MAIN for row in heads)


def test_savage_weapon_allocated_to_alt_excludes_that_alt_from_tome_opportunity():
    original = split_state()
    mains = tuple(with_needs(person, set()) for person in original.mains)
    proposal = generate_split_plan_v2(replace(original, mains=mains))
    for group in proposal.groups:
        coffer = next(row for row in group.assignments if row.loot_key == "WEAPON_COFFER")
        paired = [
            row
            for row in group.assignments
            if row.loot_key in {"WEAPON_TOMESTONE", "WEAPON_AUGMENT"}
        ]
        assert len(paired) == 2
        assert paired[0].recipient_id == paired[1].recipient_id != coffer.recipient_id
        assert all(row.recipient_kind is CharacterKind.ALT for row in paired)


def test_ring_coffer_can_satisfy_ring_two_without_replacing_completed_ring_one():
    main = with_needs(character(1, 1, CharacterKind.MAIN, position=1), {Slot.RING_2})
    proposal = generate_regular_plan_v2(state(mains=(main,)))
    ring = next(row for row in proposal.assignments if row.loot_type == "ACCESSORY_COFFER")
    assert ring.primary_slot is Slot.RING_2
    assert [effect.slot_key for effect in ring.gear_effects] == [Slot.RING_2]
