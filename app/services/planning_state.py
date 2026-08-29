from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    Character,
    CharacterKind,
    ReclearGroup,
    ReclearParticipant,
    ReclearWeek,
    ReclearWorkflowState,
    V2Confirmation,
    V2Correction,
    V2Plan,
    WeeklyLockout,
)
from app.schemas.planning_state import (
    PlanningCharacter,
    PlanningFairness,
    PlanningFloor,
    PlanningGroup,
    PlanningLockout,
    PlanningPlan,
    PlanningState,
)
from app.services.needs_v2 import calculate_characters_needs_v2


class PlanningStateError(ValueError):
    pass


def load_planning_state(session: Session, static_id: int, week_id: int) -> PlanningState:
    week = session.scalar(
        select(ReclearWeek)
        .where(ReclearWeek.id == week_id)
        .options(
            joinedload(ReclearWeek.static),
            selectinload(ReclearWeek.groups)
            .selectinload(ReclearGroup.participants)
            .joinedload(ReclearParticipant.character)
            .joinedload(Character.job),
            selectinload(ReclearWeek.participants),
            selectinload(ReclearWeek.neutral_floors),
            selectinload(ReclearWeek.hierarchy_snapshot),
        )
    )
    if week is None or week.static_id != static_id:
        raise PlanningStateError("Weekly state was not found.")
    participants = sorted(week.participants, key=lambda row: row.character_id)
    characters = sorted(
        (
            character
            for member in week.static.members
            if member.active
            for character in member.characters
            if character.active
        ),
        key=lambda row: row.id,
    )
    needs = calculate_characters_needs_v2(session, [row.id for row in characters])
    needs_by_id = dict(zip((row.id for row in characters), needs, strict=True))
    hierarchy = tuple(
        (row.job_id, row.job_abbreviation, row.position)
        for row in sorted(week.hierarchy_snapshot, key=lambda item: item.position)
    )
    positions = {job_id: position for job_id, _, position in hierarchy}
    planning_characters = tuple(
        _planning_character(character, needs_by_id[character.id], positions)
        for character in characters
    )
    mains = tuple(row for row in planning_characters if row.kind is CharacterKind.MAIN)
    alts = tuple(row for row in planning_characters if row.kind is CharacterKind.ALT)
    if not mains:
        raise PlanningStateError("Planning state requires active Main characters.")
    if week.clear_mode.value == "SPLIT":
        if len(mains) != 8 or len(alts) != 8:
            raise PlanningStateError("Split planning requires eight active Mains and Alts.")
        if any(row.combat_role not in {"TANK", "HEALER", "DPS"} for row in planning_characters):
            raise PlanningStateError("Every planning character requires a valid combat role.")
        if any(
            main.job_id != alt.job_id
            for main in mains
            for alt in alts
            if main.member_id == alt.member_id
        ):
            raise PlanningStateError("Each Split Main and Alt must use the same job.")
    ownership = tuple(
        (main.character_id, alt.character_id)
        for main in mains
        for alt in alts
        if main.member_id == alt.member_id
    )
    if week.clear_mode.value == "SPLIT" and len(ownership) != 8:
        raise PlanningStateError("Split planning requires exactly eight Main/Alt ownership pairs.")
    if len({main_id for main_id, _ in ownership}) != len(ownership) or len(
        {alt_id for _, alt_id in ownership}
    ) != len(ownership):
        raise PlanningStateError("Split ownership contains duplicate bindings.")
    groups = tuple(
        PlanningGroup(
            group.id,
            group.group_number,
            tuple(
                item.character_id
                for item in sorted(group.participants, key=lambda row: row.character_id)
            ),
        )
        for group in sorted(week.groups, key=lambda row: row.group_number)
    )
    lockouts = tuple(
        PlanningLockout(row.character_id, row.floor_number, row.cleared, row.loot_eligible)
        for row in session.scalars(
            select(WeeklyLockout).where(WeeklyLockout.week_start == week.week_start)
        )
    )
    floors = tuple(
        PlanningFloor(row.floor_number, False, tuple(item.character_id for item in participants))
        for row in week.neutral_floors
    )
    plan = session.scalar(select(V2Plan).where(V2Plan.reclear_week_id == week.id))
    fairness = _fairness(session, tuple(item.character_id for item in planning_characters))
    return PlanningState(
        static_id,
        week.static.name,
        week.id,
        week.week_start.isocalendar().week,
        week.week_start,
        week.workflow_state,
        week.clear_mode,
        week.week_start,
        mains,
        alts,
        ownership,
        groups,
        floors,
        lockouts,
        hierarchy,
        PlanningPlan(plan.id, "ACTIVE", week.clear_mode) if plan else None,
        fairness,
        tuple(),
    )


def _planning_character(character, needs, positions):
    return PlanningCharacter(
        character.id,
        character.static_member_id,
        character.name,
        character.world,
        character.kind,
        character.job_id,
        character.job.abbreviation,
        character.job.uses_offhand,
        _role(character.job.role),
        positions.get(character.job_id),
        needs,
    )


def _role(value):
    return {
        "Tank": "TANK",
        "Healer": "HEALER",
        "Melee DPS": "DPS",
        "Physical Ranged DPS": "DPS",
        "Magical Ranged DPS": "DPS",
    }.get(value, value.upper() if value else None)


def _fairness(session, character_ids):
    rows = session.scalars(
        select(V2Confirmation).where(
            V2Confirmation.recipient_id.in_(character_ids),
            V2Confirmation.action == "RECEIPT",
        )
    ).all()
    corrections = {
        row.confirmation_id: row.corrected_success
        for row in session.scalars(
            select(V2Correction).where(V2Correction.correction_type == "RECEIPT_OUTCOME")
        )
    }
    savage = {character_id: 0 for character_id in character_ids}
    materials = {character_id: {} for character_id in character_ids}
    for row in rows:
        if corrections.get(row.id, row.success) is not True:
            continue
        if row.resource_key.endswith("_GLAZE") or row.resource_key.endswith("_TWINE"):
            bucket = materials[row.recipient_id]
            bucket[row.resource_key] = bucket.get(row.resource_key, 0) + row.quantity
        elif row.resource_key.endswith("_COFFER") or row.resource_key.startswith("SAVAGE_"):
            savage[row.recipient_id] += row.quantity
    return tuple(
        PlanningFairness(
            character_id, savage[character_id], tuple(sorted(materials[character_id].items()))
        )
        for character_id in character_ids
    )


def load_active_planning_state(session: Session, static_id: int) -> PlanningState:
    weeks = list(
        session.scalars(
            select(ReclearWeek).where(
                ReclearWeek.static_id == static_id,
                ReclearWeek.workflow_state.not_in(
                    [ReclearWorkflowState.CLOSED, ReclearWorkflowState.CANCELLED]
                ),
            )
        )
    )
    if len(weeks) != 1:
        raise PlanningStateError("Exactly one active weekly state is required.")
    return load_planning_state(session, static_id, weeks[0].id)
