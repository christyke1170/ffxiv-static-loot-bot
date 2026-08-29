"""Pure, tier-neutral Split V2 loot proposal calculation."""

from hashlib import sha256
from itertools import combinations

from app.domain import loot_rules
from app.models import CharacterKind, ClearMode, GearClassification, GearSlotCode
from app.schemas.needs_v2 import NeedsV2Status
from app.schemas.planning_state import PlanningState
from app.schemas.regular_planning_v2 import ProposedGearEffect
from app.schemas.split_planning_v2 import (
    SplitAssignment,
    SplitGroupProposal,
    SplitPartitionScore,
    SplitPlanningV2Error,
    SplitPlanProposal,
    SplitScore,
    UnassignedSplitLoot,
)


def generate_split_plan_v2(state: PlanningState) -> SplitPlanProposal:
    """Generate a deterministic Split proposal without SQL, writes, or mutation."""
    if not isinstance(state, PlanningState):
        raise TypeError("state must be a PlanningState")
    if state.mode is not ClearMode.SPLIT:
        raise SplitPlanningV2Error("Split V2 planning requires Split planning state.")
    warnings = list(state.warnings)
    characters = {row.character_id: row for row in (*state.mains, *state.alts)}
    ownership = {main: alt for main, alt in state.ownership}
    reverse_ownership = {alt: main for main, alt in state.ownership}
    warnings.extend(_validate_roster(state, characters, ownership, reverse_ownership))
    _validate_saved_groups(state, characters)
    mains = tuple(sorted(state.mains, key=lambda row: row.character_id))
    candidates = []
    # A partition and its complement are the same canonical split.  Anchor the
    # lowest roster Main in Run A to enumerate exactly C(8, 4) / 2 = 35.
    anchor = mains[0].character_id
    for ordinal, selected in enumerate(
        (rows for rows in combinations(mains, 4) if anchor in {row.character_id for row in rows}),
        1,
    ):
        group_a_mains = {row.character_id for row in selected}
        selected_alt_ids = {ownership[row.character_id] for row in selected}
        group_a_ids = tuple(sorted(group_a_mains | set(ownership.values()) - selected_alt_ids))
        group_b_ids = tuple(sorted(set(characters) - set(group_a_ids)))
        candidate_groups = []
        missing = []
        for number, ids in ((1, group_a_ids), (2, group_b_ids)):
            group_id = _label_group(state, number)
            assignments, absent = _plan_group(
                state,
                group_id,
                number,
                tuple(characters[i] for i in ids),
                ownership,
                reverse_ownership,
            )
            candidate_groups.append(SplitGroupProposal(group_id, number, ids, assignments))
            missing.extend(absent)
        if all(
            _role_counts(group, characters) == {"TANK": 2, "HEALER": 2, "DPS": 4}
            for group in (group_a_ids, group_b_ids)
        ):
            candidates.append(
                (
                    _partition_score(state, candidate_groups, ordinal),
                    tuple(candidate_groups),
                    tuple(missing),
                )
            )
    if not candidates:
        raise SplitPlanningV2Error(
            "Split V2 planning requires exactly eight Main/Alt ownership pairs."
        )
    score, groups, unassigned = max(candidates, key=lambda row: row[0].comparison_key)
    saved_ids = {id for group in state.groups for id in group.character_ids}
    saved_partition = tuple(
        frozenset(group.character_ids)
        for group in sorted(state.groups, key=lambda row: row.group_number)
    )
    chosen_partition = tuple(frozenset(group.participant_ids) for group in groups)
    if saved_ids and saved_partition != chosen_partition:
        warnings.append("Saved Split groups differ from the automatically optimized partition.")
    proposal_data = (
        state.static_id,
        state.week_id,
        state.week_number,
        state.mode.value,
        tuple(groups),
        tuple(unassigned),
        tuple(dict.fromkeys(warnings)),
        score,
        len(candidates),
        state.static_name,
        state.week_start,
    )
    return SplitPlanProposal(
        state.static_id,
        state.week_id,
        state.week_number,
        state.mode,
        sha256(repr(proposal_data).encode()).hexdigest(),
        tuple(groups),
        tuple(unassigned),
        tuple(dict.fromkeys(warnings)),
        score,
        len(candidates),
        state.static_name,
        state.week_start,
    )


def _validate_roster(state, characters, ownership, reverse_ownership):
    warnings = []
    if (
        len(state.ownership) != 8
        or len({main_id for main_id, _ in state.ownership}) != 8
        or len({alt_id for _, alt_id in state.ownership}) != 8
        or len(ownership) != 8
        or len(characters) != 16
    ):
        raise SplitPlanningV2Error("Split V2 planning requires exactly eight Main/Alt pairs.")
    for main_id, alt_id in ownership.items():
        main, alt = characters.get(main_id), characters.get(alt_id)
        if (
            main is None
            or alt is None
            or main.kind is not CharacterKind.MAIN
            or alt.kind is not CharacterKind.ALT
        ):
            raise SplitPlanningV2Error("Split ownership must contain eight active Main/Alt pairs.")
        if main.job_id != alt.job_id:
            raise SplitPlanningV2Error(
                f"Main/Alt job mismatch for ownership pair {main_id}/{alt_id}: "
                f"{main.job_abbreviation} != {alt.job_abbreviation}."
            )
    if any(row.combat_role not in {"TANK", "HEALER", "DPS"} for row in characters.values()):
        raise SplitPlanningV2Error("Every Split character must have a Tank, Healer, or DPS role.")
    return warnings


def _validate_saved_groups(state, characters):
    if state.groups and len(state.groups) != 2:
        raise SplitPlanningV2Error(
            "Split V2 planning requires exactly two saved groups when groups exist."
        )
    if not state.groups:
        return
    all_ids = []
    for group in state.groups:
        ids = tuple(group.character_ids)
        if len(ids) != 8 or len(set(ids)) != 8:
            raise SplitPlanningV2Error(
                f"Saved group {group.group_number} must contain eight unique participants."
            )
        if any(character_id not in characters for character_id in ids):
            raise SplitPlanningV2Error(
                f"Saved group {group.group_number} contains an unknown participant."
            )
        all_ids.extend(ids)
    if len(all_ids) != len(set(all_ids)):
        raise SplitPlanningV2Error("Saved Split groups contain duplicate participants.")


def _role_counts(ids, characters):
    return {
        role: sum(characters[character_id].combat_role == role for character_id in ids)
        for role in ("TANK", "HEALER", "DPS")
    }


def _label_group(state, number):
    return next((group.group_id for group in state.groups if group.group_number == number), number)


def _partition_score(state, groups, ordinal):
    hierarchy = {job_id: position for job_id, _, position in state.hierarchy}
    width = max(hierarchy.values(), default=0) + 1
    assignments = tuple(row for group in groups for row in group.assignments)
    savage = tuple(
        row for row in assignments if row.gear_effects and row.loot_key.endswith("COFFER")
    )

    def vector(kind):
        return tuple(
            sum(row.recipient_kind is kind and row.hierarchy_position == position for row in savage)
            for position in range(1, width)
        )

    alt_vector = vector(CharacterKind.ALT)
    return SplitPartitionScore(
        vector(CharacterKind.MAIN),
        _material_quality(assignments),
        _completed_dps_separation(state, groups, hierarchy),
        sum(alt_vector),
        alt_vector,
        sum(
            row.loot_key == "WEAPON_TOMESTONE" and row.recipient_id is not None
            for row in assignments
        ),
        ordinal,
    )


def _material_quality(assignments):
    result = []
    for material in ("ARMOR_TWINE", "ACCESSORY_GLAZE"):
        rows = sorted(
            (row for row in assignments if row.material_key == material),
            key=lambda row: row.group_number,
        )
        result.append(sum(row.recipient_id is not None for row in rows))
        for row in rows:
            score = row.score
            result.extend(
                (0, 0, 0, 0, 0, 0)
                if score is None
                else (
                    -score.combined_fairness_count,
                    score.remaining_need,
                    -score.hierarchy_position,
                    -score.fairness_count,
                    -score.member_id,
                    -score.character_id,
                )
            )
    return tuple(result)


def _completed_dps_separation(state, groups, hierarchy):
    run_by_id = {
        character_id: group.group_number
        for group in groups
        for character_id in group.participant_ids
    }
    effects = {
        (row.recipient_id, effect.slot_key)
        for group in groups
        for row in group.assignments
        if row.recipient_kind is CharacterKind.MAIN
        for effect in row.gear_effects
        if effect.resulting_category is GearClassification.SAVAGE
    }
    completed = []
    for character in state.mains:
        if character.combat_role != "DPS":
            continue
        incomplete = {
            row.gear_slot
            for row in character.needs.slot_results
            if row.desired is GearClassification.SAVAGE
            and row.status
            in {NeedsV2Status.NEEDS_SAVAGE_DROP, NeedsV2Status.OWNED_COFFER_AVAILABLE}
        }
        if all((character.character_id, slot) in effects for slot in incomplete):
            completed.append(character)
    completed.sort(
        key=lambda row: (hierarchy.get(row.job_id, 10_000), row.member_id, row.character_id)
    )
    if not completed:
        return ()
    first_run = run_by_id[completed[0].character_id]
    return tuple(int(run_by_id[row.character_id] != first_run) for row in completed[1:])


def _plan_group(state, group_id, group_number, participants, ownership, reverse_ownership):
    used: set[tuple[int, GearSlotCode]] = set()
    assignments: list[SplitAssignment] = []
    missing: list[UnassignedSplitLoot] = []
    for drop in loot_rules.REGULAR_DROPS:
        candidates = _candidates(state, participants, drop, used, ownership, reverse_ownership)
        if not candidates:
            if drop.material_type is not None:
                assignments.append(
                    SplitAssignment(
                        group_id,
                        group_number,
                        drop.floor,
                        drop.loot_type,
                        None,
                        drop.material_type,
                        None,
                        None,
                        None,
                        None,
                        None,
                        0,
                        0,
                        (),
                        1,
                        None,
                        "No Main needs this material; free-for-all.",
                    )
                )
                continue
            missing.append(
                UnassignedSplitLoot(
                    group_id,
                    group_number,
                    drop.floor,
                    drop.loot_type,
                    drop.slot,
                    drop.material_type,
                    "No participating character has a matching incomplete need.",
                )
            )
            continue
        character, score, primary_slot, effects, fairness = min(
            candidates, key=lambda row: row[1].comparison_key
        )
        owner_alt = (
            ownership.get(character.character_id)
            if character.kind is CharacterKind.MAIN
            else reverse_ownership.get(character.character_id)
        )
        assignments.append(
            SplitAssignment(
                group_id,
                group_number,
                drop.floor,
                drop.loot_type,
                primary_slot,
                drop.material_type,
                character.character_id,
                character.job_abbreviation,
                character.kind,
                owner_alt,
                character.hierarchy_position,
                score.assignments_in_proposal,
                fairness,
                effects,
                1 if drop.material_type else 0,
                score,
                f"Assigned to {character.kind.value.title()} {character.job_abbreviation}; "
                f"hierarchy {score.hierarchy_position}, fairness {fairness}.",
            )
        )
        if primary_slot is not None:
            used.add((character.character_id, primary_slot))
    tome = _plan_tome_resources(state, group_id, group_number, participants)
    if tome is not None:
        assignments.extend(tome)
    return tuple(assignments), tuple(missing)


def _plan_tome_resources(state, group_id, group_number, participants):
    eligible = []
    hierarchy = {job_id: position for job_id, _, position in state.hierarchy}
    for character in participants:
        if character.kind is not CharacterKind.ALT or not _floor_eligible(
            state, character.character_id, 3
        ):
            continue
        weapon = next(
            (row for row in character.needs.slot_results if row.gear_slot is GearSlotCode.WEAPON),
            None,
        )
        if weapon is not None and weapon.current is not GearClassification.AUGMENTED_TOME:
            eligible.append(character)
    recipient = min(
        eligible,
        key=lambda row: (hierarchy.get(row.job_id, 10_000), row.member_id, row.character_id),
        default=None,
    )
    effects = ()
    if recipient is not None:
        effects = (ProposedGearEffect(GearSlotCode.WEAPON, GearClassification.AUGMENTED_TOME),)
        if recipient.uses_offhand:
            effects += (
                ProposedGearEffect(GearSlotCode.OFFHAND, GearClassification.AUGMENTED_TOME),
            )
    common = dict(
        group_id=group_id,
        group_number=group_number,
        floor_number=3,
        primary_slot=GearSlotCode.WEAPON,
        material_key=None,
        recipient_id=recipient.character_id if recipient else None,
        recipient_job=recipient.job_abbreviation if recipient else None,
        recipient_kind=recipient.kind if recipient else None,
        owned_alt_id=None,
        hierarchy_position=hierarchy.get(recipient.job_id) if recipient else None,
        assignments_in_proposal=0,
        fairness_count=0,
        gear_effects=effects,
        resource_quantity=1,
        score=None,
        reason=(
            "Paired Tome opportunity; "
            + (
                "free-for-all."
                if recipient is None
                else "assigned to the highest-priority eligible Alt."
            )
        ),
    )
    return (
        SplitAssignment(loot_key="WEAPON_TOMESTONE", **common),
        SplitAssignment(loot_key="WEAPON_AUGMENT", **common),
    )


def _candidates(state, participants, drop, used, ownership, reverse_ownership):
    result = []
    for character in participants:
        if drop.material_type is not None and character.kind is CharacterKind.ALT:
            continue
        if not _floor_eligible(state, character.character_id, drop.floor):
            continue
        needs = character.needs
        if needs is None:
            continue
        if drop.slot is None:
            rows = [
                row
                for row in needs.slot_results
                if row.desired is GearClassification.AUGMENTED_TOME
                and row.status in {NeedsV2Status.NEEDS_BASE_TOME, NeedsV2Status.NEEDS_AUGMENTATION}
                and row.slot_name in _material_slots(needs, drop.material_type)
            ]
            required = next(
                (row for row in needs.material_needs if row.material_code == drop.material_type),
                None,
            )
            if required is None or required.additional_needed <= 0 or not rows:
                continue
            primary_slot = None
            effects = ()
            fairness = _fairness(state, character.character_id, drop.material_type)
            combined_fairness = _combined_material_fairness(state, character.character_id)
            remaining_need = required.additional_needed
        else:
            eligible_slots = (
                (drop.slot, GearSlotCode.OFFHAND)
                if drop.slot is GearSlotCode.WEAPON and character.uses_offhand
                else (drop.slot,)
            )
            rows = [
                row
                for row in needs.slot_results
                if row.gear_slot in eligible_slots
                and row.desired is GearClassification.SAVAGE
                and row.status
                in {NeedsV2Status.NEEDS_SAVAGE_DROP, NeedsV2Status.OWNED_COFFER_AVAILABLE}
            ]
            if not rows or any((character.character_id, row.gear_slot) in used for row in rows):
                continue
            primary_slot = min(rows, key=lambda row: row.sort_order).gear_slot
            effects = tuple(
                ProposedGearEffect(slot, GearClassification.SAVAGE)
                for slot in (
                    (GearSlotCode.WEAPON, GearSlotCode.OFFHAND)
                    if drop.slot is GearSlotCode.WEAPON and character.uses_offhand
                    else (drop.slot,)
                )
            )
            fairness = 0
            combined_fairness = 0
            remaining_need = 0
        hierarchy = (
            character.hierarchy_position or max((row[2] for row in state.hierarchy), default=0) + 1
        )
        score = SplitScore(
            0 if character.kind is CharacterKind.MAIN else 1,
            hierarchy,
            0,
            fairness,
            character.member_id,
            character.character_id,
            remaining_need,
            combined_fairness,
        )
        result.append((character, score, primary_slot, effects, fairness))
    return result


def _material_slots(needs, material):
    required = next((row for row in needs.material_needs if row.material_code == material), None)
    return required.slot_names if required else ()


def _fairness(state, character_id, material):
    row = next((item for item in state.fairness if item.character_id == character_id), None)
    if row is None:
        return 0
    return row.savage_receipts if material is None else dict(row.material_grants).get(material, 0)


def _combined_material_fairness(state, character_id):
    row = next((item for item in state.fairness if item.character_id == character_id), None)
    return 0 if row is None else sum(dict(row.material_grants).values())


def _floor_eligible(state, character_id, floor_number):
    rows = [
        row
        for row in state.lockouts
        if row.character_id == character_id and row.floor_number == floor_number
    ]
    return not rows or all(row.loot_eligible and not row.cleared for row in rows)
