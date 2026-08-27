"""Weekly roster validation and deterministic loot-plan generation."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models import (
    BisSetItem,
    Character,
    CharacterKind,
    ClearMode,
    FloorLootRule,
    GearSlotCode,
    LootAssignment,
    LootAssignmentCompletionItem,
    LootAssignmentState,
    LootCategory,
    LootPlan,
    LootPlanState,
    LootReceipt,
    RaidFloor,
    ReclearGroup,
    ReclearParticipant,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    WeeklyLockout,
)
from app.schemas.needs import CharacterNeedsResult, NeedStatus, SlotNeedResult
from app.schemas.planning import (
    LootPlanGenerationError,
    PlannedDropResult,
    PlanWarning,
    RankedEligibleRecipient,
    RosterValidationResult,
    ValidationIssue,
    ValidationIssueCode,
    WeeklyPlanResult,
)
from app.services.needs import calculate_character_needs

PLAN_NAME = "Generated weekly plan"


@dataclass(slots=True)
class _SimulatedNeeds:
    result: CharacterNeedsResult
    savage: dict[tuple[int, int], list[SlotNeedResult]]
    materials: dict[int, list[SlotNeedResult]]


def validate_weekly_roster(
    session: Session, reclear_week_id: int, floor_ids: list[int] | None = None
) -> RosterValidationResult:
    """Return every roster and requested-floor lockout issue for a configured week."""
    week = _load_week(session, reclear_week_id)
    floors, floor_issues = _requested_floors(session, week, floor_ids)
    issues = list(floor_issues)
    groups = sorted(week.groups, key=lambda value: value.group_number)
    active_members = sorted(
        (member for member in week.static.members if member.active), key=lambda member: member.id
    )
    active_member_ids = {member.id for member in active_members}
    expected_groups = 1 if week.clear_mode is ClearMode.REGULAR else 2
    if len(groups) != expected_groups:
        issues.append(
            ValidationIssue(
                ValidationIssueCode.GROUP_COUNT,
                f"{week.clear_mode.value.title()} clear requires {expected_groups} group(s); "
                f"found {len(groups)}.",
            )
        )

    all_character_counts: Counter[int] = Counter()
    group_member_ids: dict[int, set[int]] = {}
    for group in groups:
        characters = [participant.character for participant in group.participants]
        member_counts = Counter(character.static_member_id for character in characters)
        character_counts = Counter(character.id for character in characters)
        group_member_ids[group.id] = set(member_counts)
        all_character_counts.update(character_counts)
        if len(characters) != 8:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.GROUP_SIZE,
                    f"Group {group.group_number} requires 8 characters; found {len(characters)}.",
                    group_id=group.id,
                )
            )
        mains = [character for character in characters if character.kind is CharacterKind.MAIN]
        alts = [character for character in characters if character.kind is CharacterKind.ALT]
        expected_mains = 8 if week.clear_mode is ClearMode.REGULAR else 4
        expected_alts = 0 if week.clear_mode is ClearMode.REGULAR else 4
        if len(mains) != expected_mains:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.MAIN_COUNT,
                    f"Group {group.group_number} requires {expected_mains} mains; "
                    f"found {len(mains)}.",
                    group_id=group.id,
                )
            )
        if len(alts) != expected_alts:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.ALT_COUNT,
                    f"Group {group.group_number} requires {expected_alts} alts; found {len(alts)}.",
                    group_id=group.id,
                )
            )
        for character in characters:
            if not character.active:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.INACTIVE_CHARACTER,
                        f"Character {character.name} is inactive.",
                        group_id=group.id,
                        character_id=character.id,
                    )
                )
            if character.static_member_id not in active_member_ids:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.FOREIGN_MEMBER,
                        f"Character {character.name} does not belong to an active static member.",
                        group_id=group.id,
                        character_id=character.id,
                    )
                )
        for member_id, count in member_counts.items():
            if count > 1:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.DUPLICATE_MEMBER,
                        f"Static member {member_id} appears {count} times in group "
                        f"{group.group_number}.",
                        group_id=group.id,
                    )
                )
        for character_id, count in character_counts.items():
            if count > 1:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.DUPLICATE_CHARACTER,
                        f"Character {character_id} appears {count} times in group "
                        f"{group.group_number}.",
                        group_id=group.id,
                        character_id=character_id,
                    )
                )

    if week.clear_mode is ClearMode.REGULAR:
        represented = group_member_ids.get(groups[0].id, set()) if groups else set()
        for member in active_members:
            if member.id not in represented:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.MISSING_MEMBER,
                        f"Active static member {member.display_name} is missing from the "
                        "regular group.",
                    )
                )
    else:
        for member in active_members:
            member_groups = [
                group for group in groups if member.id in group_member_ids.get(group.id, set())
            ]
            if len(member_groups) != 2:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.MEMBER_NOT_IN_BOTH_GROUPS,
                        f"Static member {member.display_name} must participate in both "
                        "split groups.",
                    )
                )
            configured = [
                participant.character
                for group in groups
                for participant in group.participants
                if participant.character.static_member_id == member.id
            ]
            mains = [character for character in configured if character.kind is CharacterKind.MAIN]
            alts = [character for character in configured if character.kind is CharacterKind.ALT]
            if len(mains) != 1:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.MISSING_MAIN,
                        f"Static member {member.display_name} must use exactly one main "
                        "across splits; "
                        f"found {len(mains)}.",
                    )
                )
            if len(alts) != 1:
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.MISSING_ALT,
                        f"Static member {member.display_name} must use exactly one alt "
                        "across splits; "
                        f"found {len(alts)}.",
                    )
                )
            if len(mains) == len(alts) == 1:
                main_group = next(
                    group.id
                    for group in groups
                    if any(row.character is mains[0] for row in group.participants)
                )
                alt_group = next(
                    group.id
                    for group in groups
                    if any(row.character is alts[0] for row in group.participants)
                )
                if main_group == alt_group:
                    issues.append(
                        ValidationIssue(
                            ValidationIssueCode.MAIN_ALT_NOT_OPPOSITE,
                            f"Static member {member.display_name}'s main and alt must be in "
                            "opposite groups.",
                            group_id=main_group,
                        )
                    )

    for character_id, count in all_character_counts.items():
        if count > 1:
            issues.append(
                ValidationIssue(
                    ValidationIssueCode.DUPLICATE_CHARACTER,
                    f"Character {character_id} appears {count} times in the week.",
                    character_id=character_id,
                )
            )
    _append_lockout_issues(session, week, groups, floors, issues)
    return RosterValidationResult(week, floors, issues)


def generate_weekly_loot_plan(
    session: Session, reclear_week_id: int, floor_ids: list[int] | None = None
) -> WeeklyPlanResult:
    """Validate and persist one deterministic expected-drop plan without changing inventory."""
    validation = validate_weekly_roster(session, reclear_week_id, floor_ids)
    week = validation.reclear_week
    if not week.static.active:
        raise LootPlanGenerationError("Cannot generate a plan for a deactivated static.")
    if week.workflow_state in {ReclearWorkflowState.CLOSED, ReclearWorkflowState.CANCELLED}:
        raise LootPlanGenerationError("Cannot generate a plan for a closed or cancelled week.")
    if not validation.is_valid:
        raise LootPlanGenerationError("Weekly roster validation failed.", validation)

    existing = session.scalar(
        select(LootPlan)
        .where(LootPlan.reclear_week_id == week.id, LootPlan.name == PLAN_NAME)
        .options(selectinload(LootPlan.assignments))
    )
    if existing is not None:
        return _result_from_plan(week, existing, validation, [], reused=True)

    warnings: list[PlanWarning] = []
    hierarchy = {entry.job_id: entry.position for entry in week.hierarchy_snapshot}
    missing_position = max(hierarchy.values(), default=0) + 1
    main_characters = {
        participant.character.id: participant.character
        for group in week.groups
        for participant in group.participants
        if participant.character.kind is CharacterKind.MAIN
    }
    simulated = {
        character_id: _build_simulated_needs(
            calculate_character_needs(session, character_id, week.raid_tier_id)
        )
        for character_id in main_characters
    }
    warned_jobs: set[int] = set()
    receipt_counts = _confirmed_receipt_counts(session, week.raid_tier_id)
    assignment_counts: Counter[int] = Counter()
    rules = list(
        session.scalars(
            select(FloorLootRule)
            .where(
                FloorLootRule.raid_floor_id.in_([floor.id for floor in validation.requested_floors])
            )
            .options(
                joinedload(FloorLootRule.loot_type),
                joinedload(FloorLootRule.augmentation_material_type),
            )
            .order_by(FloorLootRule.raid_floor_id, FloorLootRule.id)
        )
    )
    floor_by_id = {floor.id: floor for floor in validation.requested_floors}
    plan = LootPlan(reclear_week=week, name=PLAN_NAME, state=LootPlanState.DRAFT)
    sort_order = 0
    for group in sorted(week.groups, key=lambda value: value.group_number):
        group_mains = sorted(
            (
                participant.character
                for participant in group.participants
                if participant.character.kind is CharacterKind.MAIN
            ),
            key=lambda character: (character.static_member_id, character.id),
        )
        for rule in rules:
            for instance in range(1, rule.expected_quantity + 1):
                sort_order += 1
                ranked = _rank_recipients(
                    group_mains,
                    rule,
                    simulated,
                    hierarchy,
                    missing_position,
                    assignment_counts,
                    receipt_counts,
                    warnings,
                    warned_jobs,
                )
                winner = ranked[0] if ranked else None
                backup = ranked[1] if len(ranked) > 1 else None
                bundled: list[BisSetItem] = []
                if winner is None:
                    reason = f"No eligible main needs {rule.loot_type.name} for this floor."
                    state = LootAssignmentState.LEFTOVER
                else:
                    reason = winner.reason
                    state = LootAssignmentState.PROPOSED
                    assignment_counts[winner.character.id] += 1
                    bundled = _completion_requirements(
                        simulated[winner.character.id], rule, winner.intended_bis_set_item
                    )
                    _consume_needs(simulated[winner.character.id], rule, bundled)
                assignment = LootAssignment(
                    loot_plan=plan,
                    reclear_group=group,
                    raid_floor=floor_by_id[rule.raid_floor_id],
                    loot_type=rule.loot_type,
                    intended_character=winner.character if winner else None,
                    intended_bis_set_item=winner.intended_bis_set_item if winner else None,
                    gear_slot=winner.intended_slot if winner else None,
                    resulting_classification=(
                        winner.intended_bis_set_item.classification if winner else None
                    ),
                    suggested_recipient=winner.character if winner else None,
                    backup_recipient=backup.character if backup else None,
                    expected_drop_instance=instance,
                    quantity=1,
                    planning_reason=reason,
                    recipient_owns_base_tome_item=(
                        winner.owns_required_base_tome_item if winner else None
                    ),
                    hierarchy_position=winner.hierarchy_position if winner else None,
                    state=state,
                    sort_order=sort_order,
                )
                assignment.completion_items = [
                    LootAssignmentCompletionItem(
                        bis_set_item=requirement,
                        resulting_classification=requirement.classification,
                    )
                    for requirement in bundled
                ]
    with session.begin_nested():
        session.add(plan)
        session.flush()
    return _result_from_plan(week, plan, validation, warnings, reused=False)


def _load_week(session: Session, reclear_week_id: int) -> ReclearWeek:
    week = session.scalar(
        select(ReclearWeek)
        .where(ReclearWeek.id == reclear_week_id)
        .options(
            joinedload(ReclearWeek.static).selectinload(Static.members),
            selectinload(ReclearWeek.groups)
            .selectinload(ReclearGroup.participants)
            .joinedload(ReclearParticipant.character)
            .joinedload(Character.static_member),
            selectinload(ReclearWeek.hierarchy_snapshot),
        )
    )
    if week is None:
        raise LookupError(f"unknown reclear week id {reclear_week_id}")
    return week


def _requested_floors(
    session: Session, week: ReclearWeek, floor_ids: list[int] | None
) -> tuple[list[RaidFloor], list[ValidationIssue]]:
    floors = list(
        session.scalars(
            select(RaidFloor)
            .where(RaidFloor.raid_tier_id == week.raid_tier_id)
            .order_by(RaidFloor.floor_number)
        )
    )
    if floor_ids is None:
        return floors, []
    wanted = set(floor_ids)
    selected = [floor for floor in floors if floor.id in wanted]
    missing = wanted - {floor.id for floor in selected}
    issues = [
        ValidationIssue(
            ValidationIssueCode.INVALID_FLOOR,
            f"Floor {floor_id} does not belong to the week's raid tier.",
            raid_floor_id=floor_id,
        )
        for floor_id in sorted(missing)
    ]
    return selected, issues


def _append_lockout_issues(
    session: Session,
    week: ReclearWeek,
    groups: list[ReclearGroup],
    floors: list[RaidFloor],
    issues: list[ValidationIssue],
) -> None:
    if not groups or not floors:
        return
    character_ids = {
        participant.character_id for group in groups for participant in group.participants
    }
    lockouts = list(
        session.scalars(
            select(WeeklyLockout).where(
                WeeklyLockout.character_id.in_(character_ids),
                WeeklyLockout.raid_floor_id.in_([floor.id for floor in floors]),
                WeeklyLockout.week_start == week.week_start,
                (WeeklyLockout.cleared.is_(True) | WeeklyLockout.loot_eligible.is_(False)),
            )
        )
    )
    for lockout in lockouts:
        for group in groups:
            if any(row.character_id == lockout.character_id for row in group.participants):
                issues.append(
                    ValidationIssue(
                        ValidationIssueCode.FLOOR_LOCKOUT,
                        f"Character {lockout.character_id} is locked out of floor "
                        f"{lockout.raid_floor_id} in group {group.group_number}.",
                        group_id=group.id,
                        character_id=lockout.character_id,
                        raid_floor_id=lockout.raid_floor_id,
                    )
                )


def _build_simulated_needs(result: CharacterNeedsResult) -> _SimulatedNeeds:
    savage: dict[tuple[int, int], list[SlotNeedResult]] = defaultdict(list)
    materials: dict[int, list[SlotNeedResult]] = defaultdict(list)
    if result.selected_bis_set is None or not result.selected_bis_set.active:
        return _SimulatedNeeds(result, savage, materials)
    for row in result.slot_results:
        if (
            row.status is NeedStatus.NEEDS_SAVAGE_DROP
            and row.required_raid_floor is not None
            and row.required_loot_type is not None
        ):
            savage[(row.required_raid_floor.id, row.required_loot_type.id)].append(row)
    for aggregate in result.augmentation_needs:
        eligible_rows = [
            row
            for row in result.slot_results
            if row.required_augmentation_material is aggregate.material
            and row.status in {NeedStatus.NEEDS_BASE_TOME_ITEM, NeedStatus.NEEDS_AUGMENTATION}
        ]
        materials[aggregate.material.id] = eligible_rows[: aggregate.additional_units_needed]
    return _SimulatedNeeds(result, savage, materials)


def _rank_recipients(
    characters: list[Character],
    rule: FloorLootRule,
    simulated: dict[int, _SimulatedNeeds],
    hierarchy: dict[int, int],
    missing_position: int,
    assignment_counts: Counter[int],
    receipt_counts: Counter[int],
    warnings: list[PlanWarning],
    warned_jobs: set[int],
) -> list[RankedEligibleRecipient]:
    ranked: list[RankedEligibleRecipient] = []
    for character in characters:
        state = simulated[character.id]
        bis_set = state.result.selected_bis_set
        if bis_set is None or not bis_set.active or state.result.configuration_warnings:
            continue
        rows = (
            state.materials.get(rule.augmentation_material_type_id, [])
            if rule.loot_type.category is LootCategory.AUGMENTATION_MATERIAL
            else state.savage.get((rule.raid_floor_id, rule.loot_type_id), [])
        )
        if not rows:
            continue
        row = rows[0]
        job = bis_set.job
        position = hierarchy.get(job.id, missing_position)
        if job.id not in hierarchy and job.id not in warned_jobs:
            warned_jobs.add(job.id)
            warnings.append(
                PlanWarning(
                    "MISSING_HIERARCHY_JOB",
                    f"Job {job.abbreviation} is absent from the weekly hierarchy snapshot and "
                    "was placed after configured jobs.",
                    character_id=character.id,
                    job_id=job.id,
                )
            )
        owns_base = (
            row.base_tome_item_owned
            if rule.loot_type.category is LootCategory.AUGMENTATION_MATERIAL
            else None
        )
        base_note = (
            " Required base tome item is owned."
            if owns_base
            else " Required base tome item is not yet owned."
            if owns_base is False
            else ""
        )
        reason = (
            f"Selected as hierarchy position {position} ({job.abbreviation}); needs "
            f"{rule.loot_type.name} for {row.slot.display_name}.{base_note}"
        )
        requirement = next(
            (item for item in bis_set.items if item.gear_slot_id == row.slot.id), None
        )
        ranked.append(
            RankedEligibleRecipient(
                character,
                position,
                assignment_counts[character.id],
                receipt_counts[character.id],
                requirement,
                row.slot,
                owns_base,
                reason,
            )
        )
    return sorted(
        ranked,
        key=lambda row: (
            row.hierarchy_position,
            row.assignments_in_plan,
            row.confirmed_priority_receipts,
            row.character.static_member_id,
            row.character.id,
        ),
    )


def _completion_requirements(
    state: _SimulatedNeeds, rule: FloorLootRule, primary: BisSetItem | None
) -> list[BisSetItem]:
    """Return all exact slots completed by one configured physical drop."""
    rows = (
        state.materials.get(rule.augmentation_material_type_id, [])
        if rule.loot_type.category is LootCategory.AUGMENTATION_MATERIAL
        else state.savage.get((rule.raid_floor_id, rule.loot_type_id), [])
    )
    bis_set = state.result.selected_bis_set
    if bis_set is None:
        return []
    by_slot = {item.gear_slot_id: item for item in bis_set.items}
    requirements = [by_slot[row.slot.id] for row in rows if row.slot.id in by_slot]
    if primary is None:
        return requirements[:1]
    primary_slot = primary.gear_slot.code
    job = primary.bis_set.job
    weapon_slots = {GearSlotCode.WEAPON, GearSlotCode.OFFHAND}
    # An offhand-capable job's weapon coffer yields both weapon slots. No other
    # same-loot-type slots are bundled; they still require separate physical drops.
    if job.uses_offhand and primary_slot in weapon_slots:
        bundled = [item for item in requirements if item.gear_slot.code in weapon_slots]
        if {item.gear_slot.code for item in bundled} == weapon_slots:
            return bundled
    return [primary]


def _consume_needs(
    state: _SimulatedNeeds, rule: FloorLootRule, requirements: list[BisSetItem]
) -> None:
    rows = (
        state.materials.get(rule.augmentation_material_type_id, [])
        if rule.loot_type.category is LootCategory.AUGMENTATION_MATERIAL
        else state.savage.get((rule.raid_floor_id, rule.loot_type_id), [])
    )
    completed_slot_ids = {item.gear_slot_id for item in requirements}
    rows[:] = [row for row in rows if row.slot.id not in completed_slot_ids]


def _confirmed_receipt_counts(session: Session, raid_tier_id: int) -> Counter[int]:
    rows = session.execute(
        select(LootAssignment.intended_character_id, LootReceipt.quantity)
        .join(LootReceipt, LootReceipt.loot_assignment_id == LootAssignment.id)
        .join(LootPlan, LootPlan.id == LootAssignment.loot_plan_id)
        .join(ReclearWeek, ReclearWeek.id == LootPlan.reclear_week_id)
        .where(
            ReclearWeek.raid_tier_id == raid_tier_id,
            LootAssignment.intended_character_id.is_not(None),
        )
    )
    counts: Counter[int] = Counter()
    for character_id, quantity in rows:
        counts[character_id] += quantity
    return counts


def _result_from_plan(
    week: ReclearWeek,
    plan: LootPlan,
    validation: RosterValidationResult,
    warnings: list[PlanWarning],
    *,
    reused: bool,
) -> WeeklyPlanResult:
    assignments = [
        PlannedDropResult(
            assignment=row,
            reclear_week=week,
            group=row.reclear_group,
            floor=row.raid_floor,
            loot_type=row.loot_type,
            drop_instance_number=row.expected_drop_instance,
            suggested_recipient=row.suggested_recipient,
            intended_recipient=row.intended_character,
            intended_bis_slot=(
                row.intended_bis_set_item.gear_slot if row.intended_bis_set_item else None
            ),
            backup_recipient=row.backup_recipient,
            state=row.state,
            reason=row.planning_reason or "",
            recipient_owns_required_base_tome_item=row.recipient_owns_base_tome_item,
            hierarchy_position=row.hierarchy_position,
        )
        for row in sorted(plan.assignments, key=lambda value: value.sort_order)
    ]
    return WeeklyPlanResult(week, plan, validation, assignments, warnings, reused)
