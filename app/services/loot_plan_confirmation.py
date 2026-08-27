"""Atomic application of persisted READY weekly loot plans."""

import json
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from app.models import (
    AuditLog,
    Character,
    CharacterAugmentationInventory,
    CharacterFloorBookBalance,
    CharacterKind,
    ClearMode,
    ConfirmedReclearMaterialGrant,
    GearClassification,
    GearSlot,
    LootAssignment,
    LootCategory,
    LootPlan,
    LootPlanParticipant,
    LootPlanRun,
    PlannedLootDisposition,
    ReclearFloorCompletion,
    ReclearGroup,
    ReclearParticipant,
    ReclearWeek,
    ReclearWorkflowState,
    WeeklyLockout,
    WeeklyLootPlanStatus,
)
from app.schemas.loot_plan_confirmation import (
    LootPlanConfirmationError,
    LootPlanConfirmationResult,
    LootPlanIntegrityError,
    LootPlanNotReadyError,
    LootPlanStaleError,
    LootPlanWeekConflictError,
)
from app.services.gear import set_gear
from app.services.loot_plan_lifecycle import check_loot_plan_staleness
from app.services.transactions import entity_lock


def confirm_loot_plan(session, loot_plan_id: int, actor_discord_user_id: int):
    """Validate and apply one persisted plan without committing the caller's transaction."""
    with entity_lock("loot_plan", loot_plan_id), session.begin_nested():
        plan = _load_plan(session, loot_plan_id, lock=True)
        if plan is None:
            raise LootPlanConfirmationError(f"Loot plan {loot_plan_id} was not found.")
        previous = plan.status
        if previous is WeeklyLootPlanStatus.APPLIED:
            return _result(plan, previous, True, False, 0, 0, 0, 0, 0, 0)
        if previous is not WeeklyLootPlanStatus.READY:
            raise LootPlanNotReadyError(
                f"Loot plan is {previous.value}; only READY plans can apply."
            )
        stale = check_loot_plan_staleness(session, plan.id)
        if stale.confirmation_blocked:
            raise LootPlanStaleError(
                "Loot plan source is "
                + stale.state.value
                + ": "
                + "; ".join(reason.message for reason in stale.reasons)
            )
        static = plan.reclear_week.static
        week = plan.reclear_week
        completed = _completed_week(session, static.id)
        expected = completed + 1
        target = _plan_target_week(plan)
        if target != expected or target != _week_number(session, week):
            raise LootPlanWeekConflictError(
                "Loot plan no longer targets the next uncompleted reclear week."
            )
        graph = _validate_graph(session, plan)
        applied_at = datetime.now(UTC)
        savage, twine, glaze, weapons = _apply_assignments(
            session, plan, static, graph, actor_discord_user_id
        )
        books, clears = _apply_clear_credit(session, plan, week, graph, actor_discord_user_id)
        week.workflow_state = ReclearWorkflowState.CLOSED
        week.finalized_at = applied_at
        plan.status = WeeklyLootPlanStatus.APPLIED
        plan.applied_at = applied_at
        session.add(
            AuditLog(
                static_id=static.id,
                actor_discord_user_id=actor_discord_user_id,
                action="LOOT_PLAN_APPLIED",
                entity_type="LootPlan",
                entity_id=str(plan.id),
                details=json.dumps(
                    {
                        "plan_id": plan.id,
                        "tier_id": week.raid_tier_id,
                        "target_week": target,
                        "mode": plan.mode.value,
                        "savage_gear_updates": savage,
                        "twine_grants": twine,
                        "glaze_grants": glaze,
                        "paired_alt_weapon_upgrades": weapons,
                        "book_increments": books,
                        "clear_records": clears,
                        "confirmed_at": applied_at.isoformat(),
                    }
                ),
            )
        )
        session.flush()
        return _result(plan, previous, False, True, savage, twine, glaze, weapons, books, clears)


def _load_plan(session, plan_id, lock=False):
    statement = (
        select(LootPlan)
        .where(LootPlan.id == plan_id)
        .options(
            joinedload(LootPlan.reclear_week).joinedload(ReclearWeek.static),
            joinedload(LootPlan.reclear_week).joinedload(ReclearWeek.raid_tier),
            selectinload(LootPlan.runs)
            .selectinload(LootPlanRun.participants)
            .joinedload(LootPlanParticipant.character)
            .joinedload(Character.static_member),
            selectinload(LootPlan.runs)
            .selectinload(LootPlanRun.assignments)
            .options(
                joinedload(LootAssignment.raid_floor),
                joinedload(LootAssignment.loot_type),
                joinedload(LootAssignment.intended_character),
                joinedload(LootAssignment.intended_bis_set_item),
                selectinload(LootAssignment.completion_items),
            ),
        )
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def _validate_graph(session, plan):
    runs = sorted(plan.runs, key=lambda row: row.run_number)
    expected_runs = 1 if plan.mode is ClearMode.REGULAR else 2
    if len(runs) != expected_runs:
        raise LootPlanIntegrityError("Persisted plan has an invalid run count.")
    participants = {}
    member_kinds = defaultdict(set)
    for run in runs:
        if len({p.character_id for p in run.participants}) != len(run.participants):
            raise LootPlanIntegrityError("A persisted run contains duplicate participants.")
        expected_participants = 8 if plan.mode is ClearMode.REGULAR else 8
        if len(run.participants) != expected_participants:
            raise LootPlanIntegrityError("Persisted run has an invalid participant count.")
        for participant in run.participants:
            character = participant.character
            if (
                character is None
                or character.static_member.static_id != plan.reclear_week.static_id
            ):
                raise LootPlanIntegrityError("Participant is outside the plan static.")
            if participant.designation is not character.kind:
                raise LootPlanIntegrityError(
                    "Participant designation does not match character kind."
                )
            participants[(run.id, character.id)] = participant.designation
            member_kinds[character.static_member_id].add(participant.designation)
        seen = set()
        for assignment in run.assignments:
            key = (
                assignment.raid_floor_id,
                assignment.loot_type_id,
                assignment.expected_drop_instance,
            )
            if key in seen:
                raise LootPlanIntegrityError("Persisted plan contains a duplicate physical drop.")
            seen.add(key)
            if assignment.loot_plan_id != plan.id or assignment.plan_run_id != run.id:
                raise LootPlanIntegrityError("Assignment points outside its plan or run.")
            assigned = assignment.disposition is PlannedLootDisposition.ASSIGNED
            if assigned != (assignment.intended_character_id is not None):
                raise LootPlanIntegrityError("Assignment recipient/disposition is inconsistent.")
            if assigned and (run.id, assignment.intended_character_id) not in participants:
                raise LootPlanIntegrityError("Assignment recipient is not in its run.")
            if (
                assigned
                and assignment.recipient_designation
                is not participants[(run.id, assignment.intended_character_id)]
            ):
                raise LootPlanIntegrityError("Assignment recipient designation is inconsistent.")
            if (
                assignment.loot_type.category is LootCategory.AUGMENTATION_MATERIAL
                and assigned
                and assignment.recipient_designation is not CharacterKind.MAIN
            ):
                raise LootPlanIntegrityError("Materials may only target Mains.")
    if plan.mode is ClearMode.REGULAR:
        if any(p.designation is not CharacterKind.MAIN for run in runs for p in run.participants):
            raise LootPlanIntegrityError("Regular plans cannot contain Alts.")
    else:
        if any(len(kinds) != 2 for kinds in member_kinds.values()) or len(member_kinds) != 8:
            raise LootPlanIntegrityError(
                "Split participants must contain each member's Main and Alt once."
            )
    for run in runs:
        for assignment in run.assignments:
            if assignment.paired_assignment_id is None:
                continue
            pair = next(
                (
                    candidate
                    for candidate in run.assignments
                    if candidate.id == assignment.paired_assignment_id
                ),
                None,
            )
            if pair is None or pair.paired_assignment_id != assignment.id:
                raise LootPlanIntegrityError("Weapon pair does not belong to the same run.")
            labels = {assignment.loot_type.code, pair.loot_type.code}
            if labels != {"WEAPON_TOMESTONE", "WEAPON_AUGMENT"}:
                raise LootPlanIntegrityError("Weapon pair has invalid component types.")
            if assignment.raid_floor.floor_number not in {
                2,
                3,
            } or pair.raid_floor.floor_number not in {2, 3}:
                raise LootPlanIntegrityError("Weapon pair has invalid floor sources.")
            if assignment.disposition is not pair.disposition:
                raise LootPlanIntegrityError(
                    "Weapon pair must be fully assigned or fully free roll."
                )
            if assignment.disposition is PlannedLootDisposition.ASSIGNED and (
                assignment.intended_character_id != pair.intended_character_id
                or assignment.recipient_designation is not CharacterKind.ALT
                or pair.recipient_designation is not CharacterKind.ALT
            ):
                raise LootPlanIntegrityError("Weapon pair must target one Alt.")
    return runs


def _apply_assignments(session, plan, static, runs, actor):
    counts = [0, 0, 0, 0]
    assignments = [a for run in runs for a in run.assignments]
    pairs = {}
    for assignment in assignments:
        if assignment.paired_assignment_id:
            pairs.setdefault(min(assignment.id, assignment.paired_assignment_id), []).append(
                assignment
            )
    for assignment in assignments:
        if assignment.disposition is not PlannedLootDisposition.ASSIGNED:
            continue
        recipient = assignment.intended_character
        if recipient is None:
            raise LootPlanIntegrityError("Assigned plan row has no recipient.")
        if assignment.loot_type.category is LootCategory.AUGMENTATION_MATERIAL:
            material = _material_for(session, assignment)
            row = session.scalar(
                select(ConfirmedReclearMaterialGrant).where(
                    ConfirmedReclearMaterialGrant.loot_assignment_id == assignment.id
                )
            )
            if row is None:
                inventory = _material_inventory(session, recipient.id, material.id)
                inventory.quantity += 1
                session.add(
                    ConfirmedReclearMaterialGrant(
                        loot_assignment=assignment,
                        character=recipient,
                        augmentation_material_type=material,
                        quantity=1,
                        confirmed_by_discord_user_id=actor,
                        confirmed_at=datetime.now(UTC),
                    )
                )
                counts[1 if material.code == "ARMOR_TWINE" else 2] += 1
        elif assignment.loot_type.code in {"WEAPON_TOMESTONE", "WEAPON_AUGMENT"}:
            key = min(assignment.id, assignment.paired_assignment_id or assignment.id)
            pair = pairs.get(key, [])
            if len(pair) != 2:
                raise LootPlanIntegrityError("Weapon upgrade is not a complete pair.")
            if assignment.id != min(row.id for row in pair):
                continue
            if any(row.disposition is not PlannedLootDisposition.ASSIGNED for row in pair):
                raise LootPlanIntegrityError("Weapon upgrade pair is half assigned.")
            if any(
                row.intended_character_id != recipient.id
                or row.recipient_designation is not CharacterKind.ALT
                for row in pair
            ):
                raise LootPlanIntegrityError("Weapon upgrade pair recipient is inconsistent.")
            slot = session.scalar(select(GearSlot).where(GearSlot.code == "WEAPON"))
            set_gear(session, static, recipient, slot, GearClassification.AUGMENTED_TOME, actor)
            counts[3] += 1
        else:
            if (
                assignment.intended_bis_set_item_id is None
                or assignment.intended_final_item_id is None
            ):
                raise LootPlanIntegrityError(
                    "Savage assignment has no exact persisted gear target."
                )
            requirement = assignment.intended_bis_set_item
            if (
                requirement is None
                or requirement.desired_item_id != assignment.intended_final_item_id
            ):
                raise LootPlanIntegrityError(
                    "Savage assignment exact item relationship is invalid."
                )
            set_gear(
                session, static, recipient, requirement.gear_slot, GearClassification.SAVAGE, actor
            )
            counts[0] += 1
    return tuple(counts)


def _apply_clear_credit(session, plan, week, runs, actor):
    floors = sorted(week.raid_tier.floors, key=lambda row: row.floor_number)
    groups = sorted(week.groups, key=lambda row: row.group_number)
    for index, run in enumerate(runs, 1):
        group = next((row for row in groups if row.group_number == index), None)
        if group is None:
            group = ReclearGroup(reclear_week=week, group_number=index)
            session.add(group)
            session.flush()
            groups.append(group)
        if not group.participants:
            group.participants = [
                ReclearParticipant(
                    reclear_week=week,
                    group=group,
                    character_id=participant.character_id,
                )
                for participant in run.participants
            ]
    groups.sort(key=lambda row: row.group_number)
    if len(groups) != len(runs):
        raise LootPlanIntegrityError("Reclear groups do not match persisted plan runs.")
    books = clears = 0
    for run, group in zip(runs, groups, strict=True):
        ids = {p.character_id for p in run.participants}
        if {p.character_id for p in group.participants} != ids:
            raise LootPlanIntegrityError("Reclear group does not match persisted run.")
        for floor in floors:
            existing = session.scalar(
                select(ReclearFloorCompletion).where(
                    ReclearFloorCompletion.reclear_week_id == week.id,
                    ReclearFloorCompletion.reclear_group_id == group.id,
                    ReclearFloorCompletion.raid_floor_id == floor.id,
                )
            )
            if existing is None:
                session.add(
                    ReclearFloorCompletion(
                        reclear_week=week,
                        reclear_group=group,
                        raid_floor=floor,
                        actor_discord_user_id=actor,
                    )
                )
                clears += 1
            for participant in group.participants:
                lockout = session.scalar(
                    select(WeeklyLockout).where(
                        WeeklyLockout.character_id == participant.character_id,
                        WeeklyLockout.raid_floor_id == floor.id,
                        WeeklyLockout.week_start == week.week_start,
                    )
                )
                if lockout is None:
                    session.add(
                        WeeklyLockout(
                            character_id=participant.character_id,
                            raid_floor=floor,
                            week_start=week.week_start,
                            cleared=True,
                            loot_eligible=True,
                        )
                    )
                balance = session.scalar(
                    select(CharacterFloorBookBalance).where(
                        CharacterFloorBookBalance.character_id == participant.character_id,
                        CharacterFloorBookBalance.raid_floor_id == floor.id,
                    )
                )
                if balance is None:
                    session.add(
                        CharacterFloorBookBalance(
                            character_id=participant.character_id, raid_floor=floor, earned=1
                        )
                    )
                    books += 1
                elif existing is None:
                    balance.earned += 1
                    books += 1
    return books, clears


def _material_for(session, assignment):
    from app.models import AugmentationMaterialType

    material = session.scalar(
        select(AugmentationMaterialType).where(
            AugmentationMaterialType.raid_tier_id == assignment.raid_floor.raid_tier_id,
            AugmentationMaterialType.code == assignment.loot_type.code,
        )
    )
    if material is None:
        code = "ARMOR_TWINE" if assignment.loot_type.code == "ARMOR_TWINE" else "ACCESSORY_GLAZE"
        material = session.scalar(
            select(AugmentationMaterialType).where(
                AugmentationMaterialType.raid_tier_id == assignment.raid_floor.raid_tier_id,
                AugmentationMaterialType.code == code,
            )
        )
    if material is None:
        raise LootPlanIntegrityError("Material assignment has no tier material definition.")
    return material


def _material_inventory(session, character_id, material_id):
    row = session.scalar(
        select(CharacterAugmentationInventory).where(
            CharacterAugmentationInventory.character_id == character_id,
            CharacterAugmentationInventory.augmentation_material_type_id == material_id,
        )
    )
    if row is None:
        row = CharacterAugmentationInventory(
            character_id=character_id, augmentation_material_type_id=material_id, quantity=0
        )
        session.add(row)
        session.flush()
    return row


def _completed_week(session, static_id):
    return 1 + (
        session.scalar(
            select(func.count())
            .select_from(ReclearWeek)
            .where(
                ReclearWeek.static_id == static_id,
                ReclearWeek.workflow_state == ReclearWorkflowState.CLOSED,
            )
        )
        or 0
    )


def _week_number(session, week):
    return _completed_week(session, week.static_id) + (
        0 if week.workflow_state is ReclearWorkflowState.CLOSED else 1
    )


def _plan_target_week(plan):
    return json.loads(plan.source_snapshot or "{}").get("scope", {}).get("target_week")


def _result(plan, previous, already, applied, savage, twine, glaze, weapons, books, clears):
    target = _plan_target_week(plan)
    completed = target if already else target - 1
    return LootPlanConfirmationResult(
        plan.id,
        previous,
        plan.status,
        applied,
        already,
        savage,
        twine,
        glaze,
        weapons,
        books,
        clears,
        completed - (1 if applied else 0),
        completed,
        plan.applied_at,
        tuple(),
    )
