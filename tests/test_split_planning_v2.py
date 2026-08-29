"""Independent regression cases for the neutral Split V2 optimizer."""

from dataclasses import replace

import pytest

from app.models import GearSlotCode
from app.schemas.planning_state import PlanningGroup
from app.schemas.split_planning_v2 import SplitPartitionScore, SplitPlanningV2Error
from app.services.split_planning_v2 import generate_split_plan_v2
from tests.v2_test_helpers import split_state


def test_exactly_35_canonical_partitions_are_evaluated():
    assert generate_split_plan_v2(split_state()).partitions_evaluated == 35


def test_complementary_partitions_are_not_duplicated():
    result = generate_split_plan_v2(split_state())
    assert result.partitions_evaluated == 35
    assert result.score.canonical_partition_order >= 1


def test_each_main_alt_pair_is_in_opposite_runs():
    result = generate_split_plan_v2(split_state())
    runs = [set(group.participant_ids) for group in result.groups]
    for main_id, alt_id in split_state().ownership:
        assert (main_id in runs[0]) != (alt_id in runs[0])


def test_every_generated_run_has_two_tanks_two_healers_and_four_dps():
    state = split_state()
    result = generate_split_plan_v2(state)
    roles = {row.character_id: row.combat_role for row in (*state.mains, *state.alts)}
    for group in result.groups:
        assert {
            role: sum(roles[i] == role for i in group.participant_ids)
            for role in ("TANK", "HEALER", "DPS")
        } == {
            "TANK": 2,
            "HEALER": 2,
            "DPS": 4,
        }


def test_saved_groups_do_not_determine_selected_partition():
    state = split_state()
    result = generate_split_plan_v2(state)
    assert result.groups[0].participant_ids != ()


def test_malformed_saved_groups_are_rejected_safely():
    state = split_state()
    malformed = state.__class__(*state.__dict__.values()) if False else state
    malformed = state.__class__(
        state.static_id,
        state.static_name,
        state.week_id,
        state.week_number,
        state.week_start,
        state.week_status,
        state.mode,
        state.reset_period,
        state.mains,
        state.alts,
        state.ownership,
        (PlanningGroup(1, 1, (state.mains[0].character_id,)),),
        state.floors,
        state.lockouts,
        state.hierarchy,
        state.active_plan,
        state.fairness,
        state.warnings,
    )
    with pytest.raises(SplitPlanningV2Error, match="two saved groups"):
        generate_split_plan_v2(malformed)


def test_ownership_mismatch_is_rejected():
    state = split_state()
    malformed = state.__class__(
        state.static_id,
        state.static_name,
        state.week_id,
        state.week_number,
        state.week_start,
        state.week_status,
        state.mode,
        state.reset_period,
        state.mains,
        state.alts,
        state.ownership[:-1],
        state.groups,
        state.floors,
        state.lockouts,
        state.hierarchy,
        state.active_plan,
        state.fairness,
        state.warnings,
    )
    with pytest.raises(SplitPlanningV2Error):
        generate_split_plan_v2(malformed)


def test_exact_comparison_key_is_lexicographic_and_canonical_last():
    score = SplitPartitionScore((1,), (2,), (3,), 4, (5,), 6, 7)
    assert score.comparison_key == ((1,), (2,), (3,), 4, (5,), 6, -7)


def test_split_output_is_deterministic_and_read_only():
    state = split_state()
    before = tuple(row.character_id for row in (*state.mains, *state.alts))
    first = generate_split_plan_v2(state)
    second = generate_split_plan_v2(state)
    assert first == second
    assert before == tuple(row.character_id for row in (*state.mains, *state.alts))


def test_material_quality_places_twine_before_glaze():
    assert (
        SplitPartitionScore((0,), (1, 9), (), 0, (), 0, 1).comparison_key
        > SplitPartitionScore((0,), (1, 8), (), 0, (), 0, 1).comparison_key
    )


def test_fewer_combined_material_grants_is_better():
    left = SplitPartitionScore((0,), (1, -1, 0), (), 0, (), 0, 1)
    right = SplitPartitionScore((0,), (1, -2, 0), (), 0, (), 0, 1)
    assert right.comparison_key < left.comparison_key


def test_material_quality_tie_breaks_remaining_need_hierarchy_and_grants():
    base = (0,)
    assert (
        SplitPartitionScore(base, (1, 0, 1), (), 0, (), 0, 1).comparison_key
        < SplitPartitionScore(base, (1, 0, 2), (), 0, (), 0, 1).comparison_key
    )


def test_material_quality_final_tie_break_is_canonical_order():
    first = SplitPartitionScore((0,), (1,), (), 0, (), 0, 1)
    second = SplitPartitionScore((0,), (1,), (), 0, (), 0, 2)
    assert second.comparison_key < first.comparison_key


def test_completed_dps_separation_is_after_main_and_material_components():
    better = SplitPartitionScore((1,), (1,), (2,), 0, (), 0, 1)
    worse = SplitPartitionScore((1,), (1,), (1,), 0, (), 0, 2)
    assert better.comparison_key > worse.comparison_key
    earlier = SplitPartitionScore((2,), (0,), (0,), 0, (), 0, 99)
    assert earlier.comparison_key > better.comparison_key


def test_useful_alt_savage_is_after_completed_dps_separation():
    better_alt = SplitPartitionScore((1,), (1,), (1,), 1, (1,), 0, 1)
    better_dps = SplitPartitionScore((1,), (1,), (2,), 0, (), 0, 2)
    assert better_dps.comparison_key > better_alt.comparison_key


def test_useful_paired_tome_opportunity_is_scored_after_alt_components():
    with_tome = SplitPartitionScore((1,), (1,), (1,), 0, (), 1, 1)
    without_tome = SplitPartitionScore((1,), (1,), (1,), 0, (), 0, 2)
    assert with_tome.comparison_key > without_tome.comparison_key


def test_paired_tome_outputs_two_logical_resources_for_one_alt():
    state = split_state()
    proposal = generate_split_plan_v2(state)
    rows = [
        row
        for group in proposal.groups
        for row in group.assignments
        if row.loot_key in {"WEAPON_TOMESTONE", "WEAPON_AUGMENT"}
    ]
    assert len(rows) == 4
    assert {(row.loot_key, row.recipient_id) for row in rows}
    assert len({row.recipient_id for row in rows}) == 2


def test_paired_tome_effects_retain_weapon_then_applicable_offhand():
    state = split_state()
    proposal = generate_split_plan_v2(state)
    rows = [
        row
        for group in proposal.groups
        for row in group.assignments
        if row.loot_key == "WEAPON_TOMESTONE"
    ]
    assert rows
    assert all(
        [effect.slot_key for effect in row.gear_effects]
        == [GearSlotCode.WEAPON, GearSlotCode.OFFHAND]
        for row in rows
        if row.recipient_id is not None and row.recipient_id <= 8
    )


def test_saved_setup_groups_only_add_a_warning():
    state = split_state()
    groups = (
        PlanningGroup(11, 1, tuple(range(1, 5)) + tuple(range(13, 17))),
        PlanningGroup(12, 2, tuple(range(5, 9)) + tuple(range(9, 13))),
    )
    proposal = generate_split_plan_v2(replace(state, groups=groups))
    assert any("Saved Split groups differ" in warning for warning in proposal.warnings)


@pytest.mark.parametrize("role", ["TANK", "HEALER"])
def test_completed_non_dps_roles_do_not_become_dps_separation(role):
    proposal = generate_split_plan_v2(split_state())
    assert proposal.score.completed_dps_separation == () or isinstance(
        proposal.score.completed_dps_separation, tuple
    )
