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
from app.services.loot_allocation import (
    floor_eligible,
    hierarchy_key,
    hierarchy_position,
    material_priority,
    savage_need,
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
    for character in characters.values():
        if character.needs:
            warnings.extend(character.needs.configuration_warnings)
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
    assignments = tuple(row for group in groups for row in group.assignments)
    savage = tuple(
        row for row in assignments if row.gear_effects and row.loot_key.endswith("COFFER")
    )

    def vector(characters):
        return tuple(
            sum(row.recipient_id == character.character_id for row in savage)
            for character in sorted(characters, key=lambda row: hierarchy_key(state, row))
        )

    alt_vector = vector(state.alts)
    characters = {row.character_id: row for row in (*state.mains, *state.alts)}
    balance = -sum(
        abs(
            sum(
                characters[identifier].kind is CharacterKind.MAIN
                and characters[identifier].combat_role == role
                for identifier in group.participant_ids
            )
            - ideal
        )
        for group in groups
        for role, ideal in (("DPS", 2), ("TANK", 1), ("HEALER", 1))
    )
    return SplitPartitionScore(
        vector(state.mains),
        _material_quality(assignments),
        (),
        sum(alt_vector),
        alt_vector,
        sum(row.loot_key == "WEAPON_TOMESTONE" for row in assignments),
        ordinal,
        main_role_balance=balance,
    )


def _material_quality(assignments):
    # Compare actual Main outcomes, independent of the arbitrary Run A/B labels.
    quality = []
    for material in ("ARMOR_TWINE", "ACCESSORY_GLAZE"):
        scores = sorted(
            (row.score for row in assignments if row.material_key == material),
            key=lambda score: score.comparison_key,
        )
        quality.append(len(scores))
        for score in scores:
            quality.extend(
                (
                    -score.combined_fairness_count,
                    score.remaining_need,
                    -score.hierarchy_position,
                    -score.fairness_count,
                )
            )
    return tuple(quality)


def _plan_group(state, group_id, group_number, participants, ownership, reverse_ownership):
    used, planned = set(), {}
    assignments, missing = [], []
    for drop in loot_rules.REGULAR_DROPS:
        candidates = []
        for character in participants:
            if character.needs is None or not floor_eligible(
                state, character.character_id, drop.floor
            ):
                continue
            if drop.material_type:
                if character.kind is not CharacterKind.MAIN:
                    continue
                priority = material_priority(state, character, drop.material_type, planned)
                if priority is None:
                    continue
                combined, negative_need, position, specific, _, _ = priority
                primary, effects = None, ()
            else:
                need = savage_need(character, drop, used)
                if need is None:
                    continue
                primary, effects = need
                combined, negative_need, specific = 0, 0, 0
                position = hierarchy_position(state, character)
            score = SplitScore(
                0 if character.kind is CharacterKind.MAIN else 1,
                position,
                0,
                specific,
                character.member_id,
                character.character_id,
                -negative_need,
                combined,
            )
            candidates.append((score, character, primary, effects))
        if not candidates:
            reason = (
                "No eligible Main needs this material; Main-only, left unassigned."
                if drop.material_type
                else "No participating Main or Alt needs this for job BiS; free-for-all."
            )
            missing.append(
                UnassignedSplitLoot(
                    group_id,
                    group_number,
                    drop.floor,
                    drop.loot_type,
                    drop.slot,
                    drop.material_type,
                    reason,
                )
            )
            continue
        score, character, primary, effects = min(candidates, key=lambda row: row[0].comparison_key)
        owner_alt = (
            ownership.get(character.character_id)
            if character.kind is CharacterKind.MAIN
            else reverse_ownership.get(character.character_id)
        )
        reason = (
            "Main-only material: combined receipts, remaining need, then hierarchy."
            if drop.material_type
            else f"Greedy {character.kind.value.title()} BiS allocation; "
            f"hierarchy {score.hierarchy_position}."
        )
        assignments.append(
            SplitAssignment(
                group_id,
                group_number,
                drop.floor,
                drop.loot_type,
                primary,
                drop.material_type,
                character.character_id,
                character.job_abbreviation,
                character.kind,
                owner_alt,
                score.hierarchy_position,
                0,
                score.fairness_count,
                effects,
                1,
                score,
                reason,
            )
        )
        used.update((character.character_id, effect.slot_key) for effect in effects)
        if drop.material_type:
            key = character.character_id, drop.material_type
            planned[key] = planned.get(key, 0) + 1
    tome, unavailable = _plan_tome_resources(state, group_id, group_number, participants, used)
    assignments.extend(tome)
    missing.extend(unavailable)
    return tuple(assignments), tuple(missing)


def _plan_tome_resources(state, group_id, group_number, participants, used):
    eligible = []
    for character in participants:
        if (
            character.kind is not CharacterKind.ALT
            or not floor_eligible(state, character.character_id, 3)
            or (character.character_id, GearSlotCode.WEAPON) in used
        ):
            continue
        weapon = next(
            (row for row in character.needs.slot_results if row.gear_slot is GearSlotCode.WEAPON),
            None,
        )
        if (
            weapon is not None
            and weapon.current not in {GearClassification.SAVAGE, GearClassification.AUGMENTED_TOME}
            and weapon.status is not NeedsV2Status.MANUALLY_COMPLETE
        ):
            eligible.append(character)
    recipient = min(eligible, key=lambda row: hierarchy_key(state, row), default=None)
    keys = ("WEAPON_TOMESTONE", "WEAPON_AUGMENT")
    if recipient is None:
        return (), tuple(
            UnassignedSplitLoot(
                group_id,
                group_number,
                3,
                key,
                GearSlotCode.WEAPON,
                None,
                "No eligible Alt needs a weapon upgrade; Alt-only, left unassigned.",
            )
            for key in keys
        )
    effects = (ProposedGearEffect(GearSlotCode.WEAPON, GearClassification.AUGMENTED_TOME),)
    if recipient.uses_offhand:
        effects += (ProposedGearEffect(GearSlotCode.OFFHAND, GearClassification.AUGMENTED_TOME),)
    return tuple(
        SplitAssignment(
            group_id,
            group_number,
            3,
            key,
            GearSlotCode.WEAPON,
            None,
            recipient.character_id,
            recipient.job_abbreviation,
            recipient.kind,
            None,
            hierarchy_position(state, recipient),
            0,
            0,
            effects if key == "WEAPON_TOMESTONE" else (),
            1,
            None,
            "Paired weapon upgrade for the highest-priority eligible Alt.",
        )
        for key in keys
    ), ()
