"""Neutral weekly reclear lifecycle used by the V2 command surface."""

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.domain import loot_rules
from app.models import (
    AuditLog,
    Character,
    CharacterKind,
    ClearMode,
    JobHierarchy,
    ReclearGroup,
    ReclearParticipant,
    ReclearWeek,
    ReclearWeekFloor,
    ReclearWorkflowState,
)
from app.services.weeks import ResetPeriodPolicy, snapshot_hierarchy


def setup_roster(session, static, mode):
    if not static.active:
        raise ValueError("A deactivated static cannot be used for new planning.")
    hierarchy = session.scalar(
        select(JobHierarchy).where(
            JobHierarchy.static_id == static.id, JobHierarchy.active_marker.is_(True)
        )
    )
    if hierarchy is None:
        raise ValueError("The selected static has no active job hierarchy.")
    members = sorted((m for m in static.members if m.active), key=lambda m: m.id)
    errors = []
    mains = {}
    alts = {}
    if len(members) != 8:
        errors.append(f"Exactly eight active members are required; found {len(members)}.")
    for member in members:
        ms = [c for c in member.characters if c.active and c.kind is CharacterKind.MAIN]
        aa = [c for c in member.characters if c.active and c.kind is CharacterKind.ALT]
        if len(ms) != 1:
            errors.append(f"{member.display_name} must have exactly one active main.")
        else:
            mains[member.id] = ms[0]
        if mode is ClearMode.SPLIT:
            if len(aa) != 1:
                errors.append(
                    f"{member.display_name} must have exactly one active alt for split mode."
                )
            else:
                alts[member.id] = aa[0]
            if (
                member.id in mains
                and member.id in alts
                and mains[member.id].job_id != alts[member.id].job_id
            ):
                errors.append(f"{member.display_name} Main and Alt must use the same job.")
    if errors:
        raise ValueError("\n".join(errors))
    return members, mains, alts


def preview_rosters(session, static, mode, split_a_main_member_ids=None):
    members, mains, alts = setup_roster(session, static, mode)
    if mode is ClearMode.REGULAR:
        return (tuple(mains[m.id] for m in members),)
    selected = split_a_main_member_ids or set()
    if len(selected) != 4 or not selected <= {m.id for m in members}:
        raise ValueError("Select exactly four active static members for Split A mains.")
    return (
        tuple(mains[m.id] if m.id in selected else alts[m.id] for m in members),
        tuple(alts[m.id] if m.id in selected else mains[m.id] for m in members),
    )


def create_reclear_week(
    session,
    static,
    mode,
    *,
    week_start=None,
    split_a_main_member_ids=None,
    notes=None,
    actor_discord_user_id=None,
):
    week_start = week_start or ResetPeriodPolicy().week_start(date.today())
    if session.scalar(
        select(ReclearWeek).where(
            ReclearWeek.static_id == static.id, ReclearWeek.week_start == week_start
        )
    ):
        raise ValueError("A reclear already exists for this static and reset period.")
    hierarchy = session.scalar(
        select(JobHierarchy).where(
            JobHierarchy.static_id == static.id, JobHierarchy.active_marker.is_(True)
        )
    )
    rosters = (
        preview_rosters(session, static, mode, split_a_main_member_ids)
        if mode is ClearMode.REGULAR or split_a_main_member_ids is not None
        else ()
    )
    week = ReclearWeek(
        static=static,
        hierarchy_id=hierarchy.id,
        week_start=week_start,
        clear_mode=mode,
        workflow_state=ReclearWorkflowState.DRAFT,
        notes=notes,
    )
    week.neutral_floors = [ReclearWeekFloor(floor_number=n) for n in loot_rules.floors()]
    with session.no_autoflush:
        snapshot_hierarchy(week, hierarchy)
        for number, roster in enumerate(rosters, 1):
            group = ReclearGroup(reclear_week=week, group_number=number)
            group.participants = [
                ReclearParticipant(reclear_week=week, group=group, character=c) for c in roster
            ]
    session.add(week)
    session.flush()
    session.add(
        AuditLog(
            static_id=static.id,
            actor_discord_user_id=actor_discord_user_id,
            action="RECLEAR_WEEK_CREATED",
            entity_type="ReclearWeek",
            entity_id=str(week.id),
            details=notes,
        )
    )
    return week


def current_week(session, static_id, today=None):
    start = ResetPeriodPolicy().week_start(today or date.today())
    row = session.scalar(
        select(ReclearWeek)
        .where(ReclearWeek.static_id == static_id, ReclearWeek.week_start == start)
        .options(
            joinedload(ReclearWeek.static),
            selectinload(ReclearWeek.groups)
            .selectinload(ReclearGroup.participants)
            .joinedload(ReclearParticipant.character),
            selectinload(ReclearWeek.hierarchy_snapshot),
        )
    )
    if row is None:
        raise ValueError("No reclear exists for the selected static and current reset period.")
    return row


def cancel_reclear_week(session, static_id, reason, actor, today=None):
    if not reason.strip():
        raise ValueError("A cancellation reason is required.")
    week = current_week(session, static_id, today)
    if week.workflow_state is ReclearWorkflowState.CLOSED:
        raise ValueError("A closed reclear cannot be cancelled.")
    if week.workflow_state is ReclearWorkflowState.CANCELLED:
        return week
    if session.scalar(
        select(__import__("app.models", fromlist=["V2Plan"]).V2Plan.id).where(
            __import__("app.models", fromlist=["V2Plan"]).V2Plan.reclear_week_id == week.id
        )
    ):
        raise ValueError("A reclear with a V2 plan cannot be cancelled.")
    week.workflow_state = ReclearWorkflowState.CANCELLED
    week.finalized_at = datetime.now(UTC)
    session.add(
        AuditLog(
            static_id=static_id,
            actor_discord_user_id=actor,
            action="RECLEAR_WEEK_CANCELLED",
            entity_type="ReclearWeek",
            entity_id=str(week.id),
            details=reason,
        )
    )
    return week


def resolve_character_name(session, static_id, name):
    return (
        session.scalar(
            select(Character)
            .join(Character.static_member)
            .where(
                Character.static_member.has(static_id=static_id), Character.name.ilike(name.strip())
            )
        )
        if name.strip()
        else None
    )
