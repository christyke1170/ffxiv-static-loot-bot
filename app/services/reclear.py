"""Weekly reclear setup, status, assignment administration, and cancellation."""

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    AuditLog,
    Character,
    CharacterFloorBookBalance,
    CharacterKind,
    ClearMode,
    DistributionError,
    JobHierarchy,
    LootAssignment,
    LootAssignmentState,
    LootPlan,
    ReclearFloorCompletion,
    ReclearGroup,
    ReclearParticipant,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
)
from app.schemas.reclear import GroupRoster, LootBoard, LootBoardRow, ReclearStatus, RosterEntry
from app.services.confirmations import confirmation_progress
from app.services.needs import calculate_character_needs
from app.services.transactions import entity_lock
from app.services.weeks import ResetPeriodPolicy, snapshot_hierarchy

TERMINAL_ASSIGNMENT_STATES = {
    LootAssignmentState.RECEIVED,
    LootAssignmentState.REDEEMED_CORRECTLY,
    LootAssignmentState.RECEIPT_FAILED,
    LootAssignmentState.REDEMPTION_ERROR,
    LootAssignmentState.CANCELLED,
}


def setup_roster(
    session: Session, static: Static, mode: ClearMode
) -> tuple[list, dict[int, Character], dict[int, Character]]:
    """Validate setup prerequisites and return active members and their sole valid main/alt."""
    if not static.active:
        raise ValueError("A deactivated static cannot be used for new planning.")
    if static.active_raid_tier is None:
        raise ValueError("The selected static has no active raid tier.")
    hierarchy = session.scalar(
        select(JobHierarchy).where(
            JobHierarchy.static_id == static.id, JobHierarchy.active_marker.is_(True)
        )
    )
    if hierarchy is None:
        raise ValueError("The selected static has no active job hierarchy.")
    members = sorted((member for member in static.members if member.active), key=lambda row: row.id)
    errors = []
    if len(members) != 8:
        errors.append(f"Exactly eight active members are required; found {len(members)}.")
    mains: dict[int, Character] = {}
    alts: dict[int, Character] = {}
    for member in members:
        valid_mains = [c for c in member.characters if c.active and c.kind is CharacterKind.MAIN]
        valid_alts = [c for c in member.characters if c.active and c.kind is CharacterKind.ALT]
        if len(valid_mains) != 1:
            errors.append(f"{member.display_name} must have exactly one active main.")
        else:
            mains[member.id] = valid_mains[0]
        if mode is ClearMode.SPLIT:
            if len(valid_alts) != 1:
                errors.append(
                    f"{member.display_name} must have exactly one active alt for split mode."
                )
            else:
                alts[member.id] = valid_alts[0]
    if errors:
        raise ValueError("\n".join(errors))
    return members, mains, alts


def preview_rosters(
    session: Session,
    static: Static,
    mode: ClearMode,
    split_a_main_member_ids: set[int] | None = None,
) -> tuple[tuple[Character, ...], ...]:
    members, mains, alts = setup_roster(session, static, mode)
    if mode is ClearMode.REGULAR:
        return (tuple(mains[member.id] for member in members),)
    selected = split_a_main_member_ids or set()
    member_ids = {member.id for member in members}
    if len(selected) != 4 or not selected <= member_ids:
        raise ValueError("Select exactly four active static members for Split A mains.")
    split_a = tuple(mains[m.id] if m.id in selected else alts[m.id] for m in members)
    split_b = tuple(alts[m.id] if m.id in selected else mains[m.id] for m in members)
    return split_a, split_b


def create_reclear_week(
    session: Session,
    static: Static,
    mode: ClearMode,
    *,
    split_a_main_member_ids: set[int] | None = None,
    notes: str | None = None,
    today: date | None = None,
    actor_discord_user_id: int | None = None,
) -> ReclearWeek:
    week_start = ResetPeriodPolicy().week_start(today or date.today())
    if session.scalar(
        select(ReclearWeek.id).where(
            ReclearWeek.static_id == static.id, ReclearWeek.week_start == week_start
        )
    ):
        raise ValueError("A reclear already exists for this static and reset period.")
    rosters = preview_rosters(session, static, mode, split_a_main_member_ids)
    participants = tuple(dict.fromkeys(character for roster in rosters for character in roster))
    hierarchy = session.scalar(
        select(JobHierarchy).where(
            JobHierarchy.static_id == static.id, JobHierarchy.active_marker.is_(True)
        )
    )
    week = ReclearWeek(
        static=static,
        raid_tier=static.active_raid_tier,
        week_start=week_start,
        clear_mode=mode,
        workflow_state=ReclearWorkflowState.DRAFT,
        notes=notes or None,
    )
    with session.no_autoflush:
        snapshot_hierarchy(week, hierarchy)
        for number, roster in enumerate(rosters, 1):
            group = ReclearGroup(reclear_week=week, group_number=number)
            group.participants = [
                ReclearParticipant(reclear_week=week, character=character) for character in roster
            ]
    session.add(week)
    session.flush()
    initialize_participant_books(session, static, participants)
    _audit(
        session,
        static.id,
        actor_discord_user_id,
        "RECLEAR_WEEK_CREATED",
        "ReclearWeek",
        week.id,
        notes,
    )
    return week


def initialize_participant_books(
    session: Session, static: Static, participants: tuple[Character, ...] | list[Character]
) -> None:
    """Seed initial Week-1 books for explicitly participating characters, idempotently."""
    tier = static.active_raid_tier
    if tier is None:
        raise ValueError("The selected static has no active raid tier.")
    for character in participants:
        for floor in tier.floors:
            row = session.scalar(
                select(CharacterFloorBookBalance).where(
                    CharacterFloorBookBalance.character_id == character.id,
                    CharacterFloorBookBalance.raid_floor_id == floor.id,
                )
            )
            if row is None:
                session.add(
                    CharacterFloorBookBalance(character=character, raid_floor=floor, earned=1)
                )
    session.flush()


def current_week(session: Session, static_id: int, today: date | None = None) -> ReclearWeek:
    week_start = ResetPeriodPolicy().week_start(today or date.today())
    row = session.scalar(
        select(ReclearWeek)
        .where(ReclearWeek.static_id == static_id, ReclearWeek.week_start == week_start)
        .options(
            joinedload(ReclearWeek.static).joinedload(Static.guild),
            joinedload(ReclearWeek.raid_tier),
            selectinload(ReclearWeek.groups)
            .selectinload(ReclearGroup.participants)
            .joinedload(ReclearParticipant.character)
            .joinedload(Character.static_member),
            selectinload(ReclearWeek.hierarchy_snapshot),
        )
    )
    if row is None:
        raise ValueError("No reclear exists for the selected static and current reset period.")
    return row


def reclear_status(session: Session, static_id: int, today: date | None = None) -> ReclearStatus:
    week = current_week(session, static_id, today)
    groups = tuple(
        GroupRoster(
            group.id,
            group.group_number,
            tuple(
                RosterEntry(
                    p.character.static_member_id,
                    p.character.static_member.display_name,
                    p.character.id,
                    p.character.name,
                    p.character.kind.value,
                )
                for p in sorted(group.participants, key=lambda row: row.character.static_member_id)
            ),
        )
        for group in sorted(week.groups, key=lambda row: row.group_number)
    )
    completions = tuple(
        (row.reclear_group.group_number, row.raid_floor.name)
        for row in session.scalars(
            select(ReclearFloorCompletion)
            .where(ReclearFloorCompletion.reclear_week_id == week.id)
            .options(
                joinedload(ReclearFloorCompletion.reclear_group),
                joinedload(ReclearFloorCompletion.raid_floor),
            )
            .order_by(ReclearFloorCompletion.raid_floor_id, ReclearFloorCompletion.reclear_group_id)
        )
    )
    plan = session.scalar(select(LootPlan).where(LootPlan.reclear_week_id == week.id))
    progress = confirmation_progress(session, week.id)
    errors = (
        session.scalar(
            select(func.count())
            .select_from(DistributionError)
            .where(DistributionError.reclear_week_id == week.id)
        )
        or 0
    )
    pending = (
        progress.pending_receipt_questions
        + progress.pending_redemption_questions
        + progress.pending_augmentation_questions
    )
    return ReclearStatus(
        week.id,
        week.static_id,
        week.static.name,
        week.static.guild.discord_guild_id,
        week.week_start,
        week.clear_mode,
        week.workflow_state,
        week.raid_tier.name,
        tuple(f"{row.position}. {row.job_abbreviation}" for row in week.hierarchy_snapshot),
        groups,
        completions,
        plan.state.value if plan else "Not generated",
        (
            f"{progress.fully_resolved_assignments}/{progress.total_planned_assignments} "
            f"resolved; {pending} pending"
        ),
        errors,
        progress.can_close
        and week.workflow_state
        in {
            ReclearWorkflowState.AWAITING_CONFIRMATION,
            ReclearWorkflowState.CONFIRMED,
            ReclearWorkflowState.CLOSED,
        },
    )


def load_loot_board(session: Session, static_id: int, today: date | None = None) -> LootBoard:
    week = current_week(session, static_id, today)
    assignments = list(
        session.scalars(
            select(LootAssignment)
            .join(LootPlan)
            .where(LootPlan.reclear_week_id == week.id)
            .options(
                joinedload(LootAssignment.raid_floor),
                joinedload(LootAssignment.reclear_group),
                joinedload(LootAssignment.loot_type),
                joinedload(LootAssignment.intended_character),
                joinedload(LootAssignment.suggested_recipient),
                joinedload(LootAssignment.final_recipient),
                joinedload(LootAssignment.backup_recipient),
                joinedload(LootAssignment.intended_final_item),
                joinedload(LootAssignment.intended_bis_set_item),
                selectinload(LootAssignment.confirmations),
            )
            .order_by(
                LootAssignment.raid_floor_id,
                LootAssignment.reclear_group_id,
                LootAssignment.sort_order,
            )
        )
    )
    error_rows = list(
        session.scalars(
            select(DistributionError).where(DistributionError.reclear_week_id == week.id)
        )
    )
    errors: dict[int, list[str]] = {}
    for error in error_rows:
        errors.setdefault(error.loot_assignment_id, []).append(error.description)
    rows = tuple(
        LootBoardRow(
            row.id,
            row.raid_floor_id,
            row.raid_floor.floor_number,
            row.raid_floor.name,
            row.reclear_group.group_number,
            row.loot_type.name,
            row.expected_drop_instance,
            _name(row.final_recipient or row.intended_character),
            _name(row.backup_recipient),
            row.state,
            row.intended_bis_set_item.gear_slot.display_name if row.intended_bis_set_item else "—",
            row.intended_final_item.name if row.intended_final_item else "—",
            _name(row.suggested_recipient),
            _name(row.final_recipient),
            row.hierarchy_position,
            row.planning_reason or "—",
            row.recipient_owns_base_tome_item,
            tuple((c.confirmation_type, c.result, c.note or "") for c in row.confirmations),
            tuple(errors.get(row.id, ())),
        )
        for row in assignments
    )
    members = frozenset(member.discord_user_id for member in week.static.members if member.active)
    return LootBoard(
        week.id,
        week.static_id,
        week.static.name,
        week.static.guild.discord_guild_id,
        week.week_start,
        rows,
        members,
    )


def override_assignment(
    session: Session,
    static_id: int,
    assignment_id: int,
    recipient_id: int,
    reason: str,
    actor: int,
    *,
    force: bool = False,
) -> LootAssignment:
    with entity_lock("assignment", assignment_id), session.begin_nested():
        if not reason.strip():
            raise ValueError("An override reason is required.")
        row = _assignment_for_static(session, static_id, assignment_id)
        if row.state in TERMINAL_ASSIGNMENT_STATES or row.receipt is not None:
            raise ValueError("Received or finalized assignments cannot be overridden.")
        recipient = session.get(Character, recipient_id)
        group_character_ids = {p.character_id for p in row.reclear_group.participants}
        if (
            recipient is None
            or recipient.id not in group_character_ids
            or recipient.kind is not CharacterKind.MAIN
        ):
            raise ValueError("The new recipient must be a main in this assignment's group.")
        needs = calculate_character_needs(
            session, recipient.id, row.loot_plan.reclear_week.raid_tier_id
        )
        relevant = any(
            not need.is_complete
            and need.required_loot_type is not None
            and need.required_loot_type.id == row.loot_type_id
            for need in needs.slot_results
        )
        if not relevant and not force:
            raise ValueError(
                "The recipient has no relevant remaining BiS need; use force=true to override."
            )
        row.final_recipient = recipient
        row.manually_overridden = True
        row.planning_reason = reason.strip()
        _audit(
            session,
            static_id,
            actor,
            "LOOT_ASSIGNMENT_OVERRIDDEN",
            "LootAssignment",
            row.id,
            reason,
        )
        session.flush()
        return row


def mark_assignment_disposition(
    session: Session,
    static_id: int,
    assignment_id: int,
    state: LootAssignmentState,
    reason: str,
    actor: int,
) -> LootAssignment:
    if state not in {LootAssignmentState.LEFTOVER, LootAssignmentState.FREE_ROLL}:
        raise ValueError("Disposition must be Leftover or Free Roll.")
    if not reason.strip():
        raise ValueError("A reason is required.")
    row = _assignment_for_static(session, static_id, assignment_id)
    if row.state in TERMINAL_ASSIGNMENT_STATES or row.receipt is not None:
        raise ValueError("Received or finalized assignments cannot be changed.")
    row.state = state
    row.planning_reason = reason.strip()
    _audit(
        session,
        static_id,
        actor,
        f"LOOT_ASSIGNMENT_{state.value}",
        "LootAssignment",
        row.id,
        reason,
    )
    session.flush()
    return row


def cancel_reclear_week(
    session: Session, static_id: int, reason: str, actor: int, today: date | None = None
) -> ReclearWeek:
    if not reason.strip():
        raise ValueError("A cancellation reason is required.")
    week = current_week(session, static_id, today)
    if week.workflow_state is ReclearWorkflowState.CANCELLED:
        return week
    if week.workflow_state is ReclearWorkflowState.CLOSED:
        raise ValueError("A closed reclear cannot be cancelled.")
    completed = session.scalar(
        select(func.count())
        .select_from(ReclearFloorCompletion)
        .where(ReclearFloorCompletion.reclear_week_id == week.id)
    )
    received = session.scalar(
        select(func.count())
        .select_from(LootAssignment)
        .join(LootPlan)
        .where(
            LootPlan.reclear_week_id == week.id,
            LootAssignment.state.in_(
                [LootAssignmentState.RECEIVED, LootAssignmentState.REDEEMED_CORRECTLY]
            ),
        )
    )
    if completed or received:
        raise ValueError("A reclear with completed floors or received loot cannot be cancelled.")
    week.workflow_state = ReclearWorkflowState.CANCELLED
    week.finalized_at = datetime.now(UTC)
    _audit(session, static_id, actor, "RECLEAR_WEEK_CANCELLED", "ReclearWeek", week.id, reason)
    return week


def resolve_assignment(session: Session, static_id: int, assignment_id: int) -> LootAssignment:
    return _assignment_for_static(session, static_id, assignment_id)


def resolve_character_name(session: Session, static_id: int, name: str) -> Character | None:
    return (
        session.scalar(
            select(Character)
            .join(Character.static_member)
            .where(
                Character.static_member.has(static_id=static_id),
                func.lower(Character.name) == name.strip().lower(),
            )
        )
        if name.strip()
        else None
    )


def _assignment_for_static(session: Session, static_id: int, assignment_id: int) -> LootAssignment:
    row = session.scalar(
        select(LootAssignment)
        .join(LootPlan)
        .join(ReclearWeek)
        .where(LootAssignment.id == assignment_id, ReclearWeek.static_id == static_id)
        .with_for_update()
        .options(
            joinedload(LootAssignment.loot_plan).joinedload(LootPlan.reclear_week),
            joinedload(LootAssignment.reclear_group).selectinload(ReclearGroup.participants),
            joinedload(LootAssignment.loot_type),
            joinedload(LootAssignment.receipt),
        )
    )
    if row is None:
        raise ValueError("That assignment does not belong to the selected static and current week.")
    current = ResetPeriodPolicy().week_start(date.today())
    if row.loot_plan.reclear_week.week_start != current:
        raise ValueError("That assignment does not belong to the selected static and current week.")
    return row


def _name(character: Character | None) -> str:
    return character.name if character else "—"


def _audit(session, static_id, actor, action, entity_type, entity_id, details):
    session.add(
        AuditLog(
            static_id=static_id,
            actor_discord_user_id=actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=details,
        )
    )
