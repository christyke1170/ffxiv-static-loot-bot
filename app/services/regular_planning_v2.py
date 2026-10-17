"""Pure Main-only Regular planning using shared greedy/fair allocation rules."""

from hashlib import sha256

from app.domain import loot_rules
from app.models import ClearMode
from app.schemas.planning_state import PlanningState
from app.schemas.regular_planning_v2 import (
    RegularAssignment,
    RegularPlanProposal,
    RegularScore,
    UnassignedRegularLoot,
)
from app.services.loot_allocation import (
    floor_eligible,
    hierarchy_key,
    hierarchy_position,
    material_priority,
    savage_need,
)


class RegularPlanningV2Error(ValueError):
    """The neutral state cannot safely produce a Regular proposal."""


def generate_regular_plan_v2(state: PlanningState) -> RegularPlanProposal:
    """Greedily allocate Savage coffers; fairly allocate Main-only Glaze/Twine."""
    if not isinstance(state, PlanningState):
        raise TypeError("state must be a PlanningState")
    if state.mode is not ClearMode.REGULAR:
        raise RegularPlanningV2Error("Regular planning requires Regular planning state.")
    if not state.mains:
        raise RegularPlanningV2Error("Regular planning requires at least one active Main.")
    warnings = list(state.warnings)
    if not state.hierarchy:
        warnings.append("No hierarchy snapshot is available; roster order is used.")
    for character in state.mains:
        if character.needs:
            warnings.extend(character.needs.configuration_warnings)
    assignments, unassigned = [], []
    used, planned = set(), {}
    for drop in loot_rules.REGULAR_DROPS:
        candidates = []
        for character in state.mains:
            if character.needs is None or not floor_eligible(
                state, character.character_id, drop.floor
            ):
                continue
            if drop.material_type:
                key = material_priority(state, character, drop.material_type, planned)
                if key is None:
                    continue
                primary, effects = None, ()
            else:
                need = savage_need(character, drop, used)
                if need is None:
                    continue
                primary, effects = need
                key = hierarchy_key(state, character)
            candidates.append((key, character, primary, effects))
        if not candidates:
            reason = (
                "No eligible Main needs this material; Main-only, left unassigned."
                if drop.material_type
                else "No eligible Main has a matching incomplete BiS need."
            )
            unassigned.append(
                UnassignedRegularLoot(
                    drop.floor, drop.loot_type, drop.slot, drop.material_type, reason
                )
            )
            continue
        key, character, primary, effects = min(candidates, key=lambda row: row[0])
        score = RegularScore(
            hierarchy_position(state, character),
            0,
            0,
            0,
            character.member_id,
            character.character_id,
            material_priority=key if drop.material_type else (),
        )
        reason = (
            "Main-only material: combined receipts, remaining need, then hierarchy."
            if drop.material_type
            else "Highest-priority eligible Main with this BiS need."
        )
        assignments.append(
            RegularAssignment(
                drop.floor,
                drop.loot_type,
                primary,
                drop.material_type,
                character.character_id,
                character.job_abbreviation,
                character.kind,
                score.hierarchy_position,
                effects,
                score,
                reason,
            )
        )
        used.update((character.character_id, effect.slot_key) for effect in effects)
        if drop.material_type:
            resource = character.character_id, drop.material_type
            planned[resource] = planned.get(resource, 0) + 1
    warnings = tuple(dict.fromkeys(warnings))
    data = (
        state.static_id,
        state.week_id,
        state.week_number,
        state.mode.value,
        tuple(assignments),
        tuple(unassigned),
        warnings,
        state.static_name,
        state.week_start,
    )
    return RegularPlanProposal(
        state.static_id,
        state.week_id,
        state.week_number,
        state.mode,
        sha256(repr(data).encode()).hexdigest(),
        tuple(assignments),
        tuple(unassigned),
        warnings,
        state.static_name,
        state.week_start,
    )
