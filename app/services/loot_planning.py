"""Pure, read-only Regular-reclear loot proposal calculation."""

from collections import Counter
from itertools import combinations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.loot_planning_config import (
    REGULAR_JOB_PRIORITY,
    REGULAR_TRACKED_DROPS,
    SPLIT_WEAPON_AUGMENT_FLOOR,
    SPLIT_WEAPON_TOMESTONE_FLOOR,
    RegularTrackedDrop,
    is_supported_combat_job,
    is_supported_regular_job,
    regular_job_priority_rank,
)
from app.models import (
    Character,
    CharacterGearSlot,
    CharacterKind,
    ConfirmedReclearMaterialGrant,
    FloorLootRule,
    GearClassification,
    GearSlot,
    GearSlotCode,
    LootCategory,
    PlannedLootDisposition,
    RaidFloor,
    RaidTier,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    StaticMember,
)
from app.schemas.loot_planning import (
    CombatRole,
    LootPlanningIssue,
    LootPlanningIssueCode,
    LootPlanningIssueSeverity,
    MaterialFairnessContext,
    PlanningRaidTier,
    PlanningStatic,
    RegularLootAssignment,
    RegularLootPlanResult,
    RegularLootPlanRun,
    RegularPlanParticipant,
    SplitCandidateRejection,
    SplitCarrySignature,
    SplitMaterialAssignment,
    SplitRejectionCode,
    SplitRoleCounts,
    SplitRosterCandidate,
    SplitRosterCandidatesResult,
    SplitRosterParticipant,
    SplitRosterRun,
    SplitSavageAssignment,
    SplitSavagePlanCandidate,
    SplitSavagePlanResult,
    SplitSavageRunnerUp,
    SplitSavageRunPlan,
    SplitWeaponUpgradeAssignment,
)
from app.schemas.needs import CharacterNeedsResult, NeedStatus
from app.services.needs import calculate_characters_needs


def calculate_regular_loot_plan(session: Session, static_id: int) -> RegularLootPlanResult:
    """Return a deterministic Regular proposal without mutating session or database state."""
    with session.no_autoflush:
        static = _load_static(session, static_id)
        if static is None:
            return _invalid(
                None,
                None,
                [
                    _error(
                        LootPlanningIssueCode.STATIC_NOT_FOUND,
                        "The selected static does not exist.",
                    )
                ],
            )
        static_result = PlanningStatic(static.name)
        tier = static.active_raid_tier
        tier_result = PlanningRaidTier(tier.name) if tier is not None else None
        issues: list[LootPlanningIssue] = []
        if not static.active:
            issues.append(
                _error(LootPlanningIssueCode.INACTIVE_STATIC, "The selected static is inactive.")
            )
        if tier is None:
            issues.append(
                _error(
                    LootPlanningIssueCode.MISSING_ACTIVE_TIER,
                    "The selected static has no active raid tier.",
                )
            )
        if issues:
            return _invalid(static_result, tier_result, issues)

        mains = _validate_roster(static, issues)
        configured = _validate_tracked_loot(tier, issues)
        if any(issue.severity is LootPlanningIssueSeverity.ERROR for issue in issues):
            return _invalid(static_result, tier_result, issues)

        needs = calculate_characters_needs(
            session,
            tuple(character.id for character in mains),
            tier.id,
            include_books=False,
        )
        _validate_needs(mains, tier, needs, issues)
        if any(issue.severity is LootPlanningIssueSeverity.ERROR for issue in issues):
            return _invalid(static_result, tier_result, issues)

        participants = tuple(
            RegularPlanParticipant(
                character_id=character.id,
                roster_order=index,
                character_name=character.name,
                world=character.world,
                job=character.job.abbreviation.upper(),
            )
            for index, character in enumerate(mains, 1)
        )
        participant_by_character = dict(zip((row.id for row in mains), participants, strict=True))
        grant_counts = _confirmed_grant_counts(session, tier.id, tuple(row.id for row in mains))
        assignments = tuple(
            _assign_drop(
                drop,
                configured[drop],
                mains,
                needs,
                participant_by_character,
                grant_counts,
            )
            for drop in REGULAR_TRACKED_DROPS
        )
        target_week = _target_week(session, static.id)
        return RegularLootPlanResult(
            static=static_result,
            active_tier=tier_result,
            target_week=target_week,
            is_valid=True,
            issues=tuple(issues),
            run=RegularLootPlanRun("Regular", participants, assignments),
        )


def generate_split_roster_candidates(
    session: Session, static_id: int
) -> SplitRosterCandidatesResult:
    """Generate every valid complementary Split roster without writes or scoring."""
    with session.no_autoflush:
        static = _load_split_static(session, static_id)
        if static is None:
            return _invalid_split(
                None,
                None,
                [
                    _error(
                        LootPlanningIssueCode.STATIC_NOT_FOUND,
                        "The selected static does not exist.",
                    )
                ],
            )
        static_result = PlanningStatic(static.name)
        tier = static.active_raid_tier
        tier_result = PlanningRaidTier(tier.name) if tier is not None else None
        issues: list[LootPlanningIssue] = []
        if not static.active:
            issues.append(
                _error(LootPlanningIssueCode.INACTIVE_STATIC, "The selected static is inactive.")
            )
        if tier is None:
            issues.append(
                _error(
                    LootPlanningIssueCode.MISSING_ACTIVE_TIER,
                    "The selected static has no active raid tier.",
                )
            )
        bindings = _validate_split_bindings(static, issues)
        if issues:
            return _invalid_split(static_result, tier_result, issues)

        candidates: list[SplitRosterCandidate] = []
        rejections: list[SplitCandidateRejection] = []
        roster_indexes = tuple(range(8))
        for ordinal, remaining_a_mains in enumerate(combinations(roster_indexes[1:], 3), 1):
            a_main_indexes = frozenset((0, *remaining_a_mains))
            identifier = "A-Mains:" + "-".join(str(index + 1) for index in sorted(a_main_indexes))
            run_a = _build_split_run("Split Run A", bindings, a_main_indexes, mains_in_set=True)
            run_b = _build_split_run("Split Run B", bindings, a_main_indexes, mains_in_set=False)
            candidate_rejections = [
                *_validate_split_run(ordinal, identifier, run_a),
                *_validate_split_run(ordinal, identifier, run_b),
                *_validate_complement(ordinal, identifier, run_a, run_b),
            ]
            if candidate_rejections:
                rejections.extend(candidate_rejections)
            else:
                candidates.append(SplitRosterCandidate(ordinal, identifier, run_a, run_b))

        if not candidates:
            issues.append(
                _error(
                    LootPlanningIssueCode.NO_VALID_SPLIT_COMPOSITION,
                    "No complementary Split candidate satisfies 2 Tanks, 2 Healers, and 4 DPS "
                    "in both runs.",
                )
            )
        return SplitRosterCandidatesResult(
            static=static_result,
            active_tier=tier_result,
            target_week=_target_week(session, static.id),
            is_valid=bool(candidates),
            issues=tuple(issues),
            total_partitions_evaluated=35,
            total_candidates_rejected=35 - len(candidates),
            candidates=tuple(candidates),
            rejections=tuple(rejections),
        )


def plan_split_savage_loot(session: Session, static_id: int) -> SplitSavagePlanResult:
    """Plan guaranteed Split Savage coffers without writing or mutating ORM state."""
    with session.no_autoflush:
        roster_result = generate_split_roster_candidates(session, static_id)
        if not roster_result.is_valid:
            return SplitSavagePlanResult(
                roster_result.static,
                roster_result.active_tier,
                roster_result.target_week,
                False,
                roster_result.issues,
            )
        static = _load_split_static(session, static_id)
        tier = static.active_raid_tier
        drops, loot_issues = _split_savage_drops(tier)
        if loot_issues:
            return SplitSavagePlanResult(
                roster_result.static,
                roster_result.active_tier,
                roster_result.target_week,
                False,
                tuple(loot_issues),
            )
        character_ids = tuple(
            participant.character_id
            for candidate in roster_result.candidates[:1]
            for run in (candidate.run_a, candidate.run_b)
            for participant in run.participants
        )
        needs = calculate_characters_needs(session, character_ids, tier.id, include_books=False)
        grant_counts = _confirmed_grant_counts(session, tier.id, character_ids)
        weapon_classifications = _weapon_classifications(session, character_ids)
        issues = list(roster_result.warnings)
        for _participant_id, result in needs.items():
            if result.selected_bis_set is None:
                issues.append(
                    _warning(
                        LootPlanningIssueCode.MISSING_BIS,
                        f"{result.character.name} has no selected BiS set for {tier.name}; "
                        "the character is loot-ineligible.",
                    )
                )
        planned = [
            _score_split_candidate(
                candidate, drops, needs, tier, grant_counts, weapon_classifications
            )
            for candidate in roster_result.candidates
        ]
        ordered = sorted(planned, key=lambda candidate: candidate.comparison_key, reverse=True)
        winner = ordered[0]
        runner_up = _runner_up(ordered[1]) if len(ordered) > 1 else None
        return SplitSavagePlanResult(
            roster_result.static,
            roster_result.active_tier,
            roster_result.target_week,
            True,
            tuple(dict.fromkeys(issues)),
            len(planned),
            winner,
            runner_up,
            _selection_reason(winner, runner_up),
        )


def _load_static(session: Session, static_id: int) -> Static | None:
    return session.scalar(
        select(Static)
        .where(Static.id == static_id)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Static.members)
            .selectinload(StaticMember.characters)
            .joinedload(Character.job),
            joinedload(Static.active_raid_tier)
            .selectinload(RaidTier.floors)
            .selectinload(RaidFloor.loot_rules)
            .options(
                joinedload(FloorLootRule.loot_type),
                joinedload(FloorLootRule.augmentation_material_type),
            ),
            joinedload(Static.active_raid_tier).selectinload(RaidTier.loot_types),
            joinedload(Static.active_raid_tier).selectinload(RaidTier.augmentation_material_types),
        )
    )


def _load_split_static(session: Session, static_id: int) -> Static | None:
    return session.scalar(
        select(Static)
        .where(Static.id == static_id)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Static.members)
            .selectinload(StaticMember.characters)
            .joinedload(Character.job),
            joinedload(Static.active_raid_tier),
            joinedload(Static.active_raid_tier)
            .selectinload(RaidTier.floors)
            .selectinload(RaidFloor.loot_rules)
            .options(
                joinedload(FloorLootRule.loot_type),
                joinedload(FloorLootRule.augmentation_material_type),
            ),
            joinedload(Static.active_raid_tier).selectinload(RaidTier.loot_types),
            joinedload(Static.active_raid_tier).selectinload(RaidTier.augmentation_material_types),
        )
    )


def _split_savage_drops(
    tier: RaidTier,
) -> tuple[tuple[RegularTrackedDrop, tuple[RaidFloor, FloorLootRule]], list[LootPlanningIssue]]:
    floors = {floor.floor_number: floor for floor in tier.floors}
    drops = []
    issues = []
    for drop in REGULAR_TRACKED_DROPS:
        if drop.category is not LootCategory.COFFER:
            continue
        floor = floors.get(drop.floor_number)
        matches = (
            [rule for rule in floor.loot_rules if rule.loot_type.code == drop.loot_type_code]
            if floor is not None
            else []
        )
        if floor is None or len(matches) != 1 or matches[0].expected_quantity != 1:
            issues.append(
                _error(
                    LootPlanningIssueCode.MISSING_LOOT_CONFIGURATION,
                    f"{tier.name} does not provide exactly one configured {drop.label} "
                    f"on Floor {drop.floor_number}.",
                )
            )
            continue
        drops.append((drop, (floor, matches[0])))
    return tuple(drops), issues


def _score_split_candidate(
    candidate: SplitRosterCandidate,
    drops: tuple[tuple[RegularTrackedDrop, tuple[RaidFloor, FloorLootRule]], ...],
    needs: dict[int, CharacterNeedsResult],
    tier: RaidTier,
    grant_counts: Counter[tuple[int, int]],
    weapon_classifications: dict[int, str | None],
) -> SplitSavagePlanCandidate:
    remaining = {character_id: _savage_need_keys(result) for character_id, result in needs.items()}
    assignments: dict[str, list[SplitSavageAssignment]] = {"Split Run A": [], "Split Run B": []}
    main_counts: Counter[str] = Counter()
    alt_counts: Counter[str] = Counter()
    for run in (candidate.run_a, candidate.run_b):
        for drop, (floor, _rule) in drops:
            winner = _select_split_recipient(run, drop, remaining)
            if winner is None:
                assignments[run.name].append(
                    SplitSavageAssignment(
                        candidate.partition_ordinal,
                        run.name,
                        floor.floor_number,
                        floor.name,
                        drop.label,
                        PlannedLootDisposition.FREE_ROLL,
                        None,
                        None,
                        None,
                        f"No participating character needs the {drop.label}.",
                    )
                )
                continue
            participant, designation = winner
            remaining[participant.character_id][(drop.floor_number, drop.loot_type_code)] -= 1
            assignments[run.name].append(
                SplitSavageAssignment(
                    candidate.partition_ordinal,
                    run.name,
                    floor.floor_number,
                    floor.name,
                    drop.label,
                    PlannedLootDisposition.ASSIGNED,
                    participant,
                    participant.job,
                    designation,
                    f"Assigned to the eligible {designation.value.title()} {participant.job} "
                    "with the highest applicable priority.",
                    _planned_bis_item_id(
                        needs[participant.character_id], floor, drop.loot_type_code
                    ),
                    _planned_bis_item_final_id(
                        needs[participant.character_id], floor, drop.loot_type_code
                    ),
                )
            )
            if designation is CharacterKind.MAIN:
                main_counts[participant.job] += 1
            else:
                alt_counts[participant.job] += 1
    main_vector = tuple(main_counts[job] for job in REGULAR_JOB_PRIORITY)
    alt_vector = tuple(alt_counts[job] for job in REGULAR_JOB_PRIORITY)
    twine_assignments, twine_score = _assign_split_material(
        candidate, needs, tier, "ARMOR_TWINE", grant_counts
    )
    glaze_assignments, glaze_score = _assign_split_material(
        candidate, needs, tier, "ACCESSORY_GLAZE", grant_counts
    )
    weapon_upgrades = _assign_split_weapon_upgrades(candidate, tier, weapon_classifications)
    carry_signature = _carry_signature(candidate, assignments, remaining)
    conflicts = _avoided_conflicts(candidate, assignments)
    comparison_key = (
        main_vector,
        twine_score,
        glaze_score,
        carry_signature.separated_completed_dps,
        sum(alt_vector),
        alt_vector,
        sum(upgrade.disposition is PlannedLootDisposition.ASSIGNED for upgrade in weapon_upgrades),
        -candidate.partition_ordinal,
    )
    return SplitSavagePlanCandidate(
        candidate.partition_ordinal,
        candidate.candidate_identifier,
        SplitSavageRunPlan(
            "Split Run A", candidate.run_a.participants, tuple(assignments["Split Run A"])
        ),
        SplitSavageRunPlan(
            "Split Run B", candidate.run_b.participants, tuple(assignments["Split Run B"])
        ),
        main_vector,
        carry_signature,
        sum(alt_vector),
        alt_vector,
        comparison_key,
        tuple(conflicts),
        twine_assignments,
        glaze_assignments,
        weapon_upgrades,
        twine_score,
        glaze_score,
        sum(upgrade.disposition is PlannedLootDisposition.ASSIGNED for upgrade in weapon_upgrades),
    )


def _savage_need_keys(result: CharacterNeedsResult) -> Counter[tuple[int, str]]:
    if result.selected_bis_set is None:
        return Counter()
    keys: Counter[tuple[int, str]] = Counter()
    for need in result.savage_loot_needs:
        keys[(need.raid_floor.floor_number, need.loot_type.code)] += need.quantity
    return keys


def _select_split_recipient(
    run: SplitRosterRun,
    drop: RegularTrackedDrop,
    remaining: dict[int, Counter[tuple[int, str]]],
) -> tuple[SplitRosterParticipant, CharacterKind] | None:
    for designation in (CharacterKind.MAIN, CharacterKind.ALT):
        eligible = [
            participant
            for participant in run.participants
            if participant.designation is designation
            and remaining.get(participant.character_id, Counter()).get(
                (drop.floor_number, drop.loot_type_code), 0
            )
            > 0
        ]
        if eligible:
            return min(
                eligible,
                key=lambda participant: (
                    regular_job_priority_rank(participant.job),
                    participant.roster_order,
                    participant.character_id,
                ),
            ), designation
    return None


def _assign_split_material(
    candidate: SplitRosterCandidate,
    needs: dict[int, CharacterNeedsResult],
    tier: RaidTier,
    material_code: str,
    grant_counts: Counter[tuple[int, int]],
) -> tuple[tuple[SplitMaterialAssignment, ...], tuple[int, ...]]:
    material = next(
        (row for row in tier.augmentation_material_types if row.code == material_code), None
    )
    floor_number = 2 if material_code == "ACCESSORY_GLAZE" else 3
    floor = next((row for row in tier.floors if row.floor_number == floor_number), None)
    if material is None or floor is None:
        return tuple(
            _free_material_assignment(candidate, run.name, floor_number, material_code, floor)
            for run in (candidate.run_a, candidate.run_b)
        ), (0, 0)
    simulated_counts = Counter(
        {key: count for key, count in grant_counts.items() if key[1] == material.id}
    )
    results: list[SplitMaterialAssignment] = []
    useful_bits: list[int] = []
    fairness_values: list[int] = []
    for run in (candidate.run_a, candidate.run_b):
        eligible = [
            participant
            for participant in run.participants
            if participant.designation is CharacterKind.MAIN
            and _remaining_material_need(needs.get(participant.character_id), material.id) > 0
        ]
        winner = (
            min(
                eligible,
                key=lambda participant: (
                    simulated_counts[(participant.character_id, material.id)],
                    -_remaining_material_need(needs.get(participant.character_id), material.id),
                    regular_job_priority_rank(participant.job),
                    participant.roster_order,
                    participant.character_id,
                ),
            )
            if eligible
            else None
        )
        if winner is None:
            results.append(
                _free_material_assignment(candidate, run.name, floor_number, material.name, floor)
            )
            useful_bits.append(0)
            fairness_values.extend((0, 0, 0, 0))
            continue
        count = simulated_counts[(winner.character_id, material.id)]
        remaining_need = _remaining_material_need(needs.get(winner.character_id), material.id)
        results.append(
            SplitMaterialAssignment(
                candidate.partition_ordinal,
                run.name,
                floor_number,
                floor.name,
                material.name,
                material_code,
                PlannedLootDisposition.ASSIGNED,
                winner,
                winner.job,
                CharacterKind.MAIN,
                count,
                remaining_need,
                f"Assigned to the eligible Main with {count} confirmed grants and "
                f"{remaining_need} remaining need.",
            )
        )
        simulated_counts[(winner.character_id, material.id)] += 1
        useful_bits.append(1)
        fairness_values.extend(
            (
                -count,
                remaining_need,
                -regular_job_priority_rank(winner.job),
                -winner.roster_order,
            )
        )
    return tuple(results), (sum(useful_bits), *fairness_values)


def _remaining_material_need(result: CharacterNeedsResult | None, material_id: int) -> int:
    if result is None:
        return 0
    return next(
        (
            need.additional_units_needed
            for need in result.augmentation_needs
            if need.material.id == material_id
        ),
        0,
    )


def _free_material_assignment(
    candidate: SplitRosterCandidate,
    run_name: str,
    floor_number: int,
    label: str,
    floor: RaidFloor | None,
) -> SplitMaterialAssignment:
    return SplitMaterialAssignment(
        candidate.partition_ordinal,
        run_name,
        floor_number,
        floor.name if floor is not None else f"Floor {floor_number}",
        label,
        "ACCESSORY_GLAZE" if floor_number == 2 else "ARMOR_TWINE",
        PlannedLootDisposition.FREE_ROLL,
        None,
        None,
        None,
        0,
        0,
        f"No participating Main has remaining need for {label}.",
    )


def _assign_split_weapon_upgrades(
    candidate: SplitRosterCandidate,
    tier: RaidTier,
    weapon_classifications: dict[int, str | None],
) -> tuple[SplitWeaponUpgradeAssignment, ...]:
    floors = {floor.floor_number: floor for floor in tier.floors}
    tomestone_floor = floors.get(2)
    augment_floor = floors.get(3)
    results = []
    for run in (candidate.run_a, candidate.run_b):
        eligible = [
            participant
            for participant in run.participants
            if participant.designation is CharacterKind.ALT
            and _alt_weapon_is_eligible(weapon_classifications.get(participant.character_id))
        ]
        winner = (
            min(
                eligible,
                key=lambda participant: (
                    regular_job_priority_rank(participant.job),
                    participant.roster_order,
                    participant.character_id,
                ),
            )
            if eligible
            else None
        )
        results.append(
            SplitWeaponUpgradeAssignment(
                candidate.partition_ordinal,
                run.name,
                winner,
                winner.job if winner is not None else None,
                CharacterKind.ALT if winner is not None else None,
                weapon_classifications.get(winner.character_id) if winner else None,
                SPLIT_WEAPON_TOMESTONE_FLOOR,
                tomestone_floor.name if tomestone_floor else "Floor 2",
                SPLIT_WEAPON_AUGMENT_FLOOR,
                augment_floor.name if augment_floor else "Floor 3",
                PlannedLootDisposition.ASSIGNED if winner else PlannedLootDisposition.FREE_ROLL,
                (
                    f"Paired Weapon Tomestone and Weapon Augment assigned to eligible Alt "
                    f"{winner.job}."
                    if winner
                    else "All participating Alts have Savage or Augmented Tome weapons."
                ),
            )
        )
    return tuple(results)


def _alt_weapon_is_eligible(classification: str | None) -> bool:
    return classification not in {
        GearClassification.SAVAGE.value,
        GearClassification.AUGMENTED_TOME.value,
    }


def _weapon_classifications(
    session: Session, character_ids: tuple[int, ...]
) -> dict[int, str | None]:
    rows = session.execute(
        select(CharacterGearSlot.character_id, CharacterGearSlot.current_classification)
        .join(CharacterGearSlot.gear_slot)
        .where(
            CharacterGearSlot.character_id.in_(character_ids),
            GearSlot.code == GearSlotCode.WEAPON,
        )
    )
    return {character_id: classification.value for character_id, classification in rows}


def _carry_signature(
    candidate: SplitRosterCandidate,
    assignments: dict[str, list[SplitSavageAssignment]],
    remaining: dict[int, Counter[tuple[int, str]]],
) -> SplitCarrySignature:
    # Compare completed Main DPS carries in base hierarchy order. The first
    # carry establishes a side; each later bit is 1 when it is on the opposite
    # side. Thus a separated completed SAM/VPR pair beats a colocated pair,
    # while Tanks, Healers, incomplete DPS, and Alt assignments are ignored.
    participant_runs = {
        participant.character_id: run_index
        for run_index, run in enumerate((candidate.run_a, candidate.run_b))
        for participant in run.participants
        if participant.designation is CharacterKind.MAIN
    }
    completed = [
        participant
        for run in (candidate.run_a, candidate.run_b)
        for participant in run.participants
        if (
            participant.designation is CharacterKind.MAIN
            and participant.combat_role is CombatRole.DPS
            and participant.job in REGULAR_JOB_PRIORITY
            and not any(
                quantity > 0
                for quantity in remaining.get(participant.character_id, Counter()).values()
            )
        )
    ]
    completed.sort(
        key=lambda participant: (
            regular_job_priority_rank(participant.job),
            participant.roster_order,
            participant.character_id,
        )
    )
    if not completed:
        return SplitCarrySignature(())
    first_run = participant_runs[completed[0].character_id]
    return SplitCarrySignature(
        tuple(
            int(participant_runs[participant.character_id] != first_run)
            for participant in completed[1:]
        )
    )


def _avoided_conflicts(
    candidate: SplitRosterCandidate, assignments: dict[str, list[SplitSavageAssignment]]
) -> list[str]:
    by_job: dict[str, set[str]] = {}
    for run_name, rows in assignments.items():
        for row in rows:
            if row.recipient_designation is CharacterKind.MAIN and row.recipient is not None:
                by_job.setdefault(row.recipient.job, set()).add(run_name)
    return [
        f"Separated {job} Main assignments across runs."
        for job in REGULAR_JOB_PRIORITY
        if len(by_job.get(job, set())) == 2
    ]


def _runner_up(candidate: SplitSavagePlanCandidate) -> SplitSavageRunnerUp:
    return SplitSavageRunnerUp(
        candidate.candidate_ordinal,
        candidate.candidate_identifier,
        candidate.main_assignment_vector,
        candidate.carry_balance_signature,
        candidate.total_useful_alt_assignments,
        candidate.alt_assignment_vector,
        candidate.twine_score,
        candidate.glaze_score,
        candidate.useful_paired_weapon_upgrades,
    )


def _selection_reason(
    winner: SplitSavagePlanCandidate, runner_up: SplitSavageRunnerUp | None
) -> str:
    if runner_up is None:
        return "Selected the only valid Split candidate."
    if winner.main_assignment_vector != runner_up.main_assignment_vector:
        return "The winner has the stronger lexicographic Main assignment vector."
    if winner.twine_score != runner_up.twine_score:
        return "Main Savage scores tied; the winner has the stronger Twine score."
    if winner.glaze_score != runner_up.glaze_score:
        return "Main Savage and Twine scores tied; the winner has the stronger Glaze score."
    if winner.carry_balance_signature != runner_up.carry_balance_signature:
        return (
            "Main assignment scores tied; the winner has the stronger completed-DPS carry balance."
        )
    if winner.total_useful_alt_assignments != runner_up.total_useful_alt_assignments:
        return "Main and carry scores tied; the winner has more useful Alt assignments."
    if winner.alt_assignment_vector != runner_up.alt_assignment_vector:
        return "Main and carry scores tied; the winner has the stronger Alt hierarchy vector."
    if winner.useful_paired_weapon_upgrades != runner_up.useful_paired_weapon_upgrades:
        return (
            "All earlier loot scores tied; the winner has more useful paired Alt weapon upgrades."
        )
    return "All meaningful scores tied; canonical candidate order selected the winner."


def _validate_split_bindings(
    static: Static, issues: list[LootPlanningIssue]
) -> list[tuple[StaticMember, Character, Character]]:
    members = sorted((member for member in static.members if member.active), key=lambda row: row.id)
    if len(members) != 8:
        issues.append(
            _error(
                LootPlanningIssueCode.INVALID_MEMBER_COUNT,
                f"Exactly eight active static members are required; found {len(members)}.",
            )
        )
    bindings: list[tuple[StaticMember, Character, Character]] = []
    for member in members:
        all_mains = sorted(
            (row for row in member.characters if row.kind is CharacterKind.MAIN),
            key=lambda row: row.id,
        )
        all_alts = sorted(
            (row for row in member.characters if row.kind is CharacterKind.ALT),
            key=lambda row: row.id,
        )
        main = _validate_one_binding(member, all_mains, CharacterKind.MAIN, issues)
        alt = _validate_one_binding(member, all_alts, CharacterKind.ALT, issues)
        for character, designation in ((main, "Main"), (alt, "Alt")):
            if character is None:
                continue
            if character.static_member_id != member.id:
                issues.append(
                    _error(
                        LootPlanningIssueCode.INVALID_MAIN_BINDING
                        if designation == "Main"
                        else LootPlanningIssueCode.INVALID_ALT_BINDING,
                        f"{character.name} is not bound to {member.display_name}.",
                    )
                )
            if character.static_member.static_id != static.id:
                issues.append(
                    _error(
                        LootPlanningIssueCode.CROSS_STATIC_CHARACTER,
                        f"{character.name} belongs to another static.",
                    )
                )
            if not is_supported_combat_job(character.job.abbreviation):
                issues.append(
                    _error(
                        LootPlanningIssueCode.UNSUPPORTED_JOB,
                        f"{character.name} has unsupported job "
                        f"{character.job.abbreviation.upper()}.",
                    )
                )
            if _combat_role(character.job.role) is None:
                issues.append(
                    _error(
                        LootPlanningIssueCode.UNKNOWN_COMBAT_ROLE,
                        f"{character.name}'s job has unknown combat role {character.job.role!r}.",
                    )
                )
        if main is not None and alt is not None:
            bindings.append((member, main, alt))
    return bindings


def _validate_one_binding(
    member: StaticMember,
    characters: list[Character],
    kind: CharacterKind,
    issues: list[LootPlanningIssue],
) -> Character | None:
    label = "Main" if kind is CharacterKind.MAIN else "Alt"
    missing_code = (
        LootPlanningIssueCode.MISSING_MAIN
        if kind is CharacterKind.MAIN
        else LootPlanningIssueCode.MISSING_ALT
    )
    duplicate_code = (
        LootPlanningIssueCode.DUPLICATE_MAIN
        if kind is CharacterKind.MAIN
        else LootPlanningIssueCode.DUPLICATE_ALT
    )
    inactive_code = (
        LootPlanningIssueCode.INACTIVE_MAIN
        if kind is CharacterKind.MAIN
        else LootPlanningIssueCode.INACTIVE_ALT
    )
    if not characters:
        issues.append(
            _error(missing_code, f"{member.display_name} has no {label} character binding.")
        )
        return None
    if len(characters) > 1:
        issues.append(
            _error(duplicate_code, f"{member.display_name} has more than one {label} binding.")
        )
        return None
    character = characters[0]
    if not character.active:
        issues.append(
            _error(inactive_code, f"{member.display_name}'s {label} character is inactive.")
        )
        return None
    return character


def _combat_role(value: str) -> CombatRole | None:
    normalized = value.strip().casefold()
    if normalized == "tank":
        return CombatRole.TANK
    if normalized == "healer":
        return CombatRole.HEALER
    if normalized in {"melee dps", "physical ranged dps", "magical ranged dps"}:
        return CombatRole.DPS
    return None


def _build_split_run(
    name: str,
    bindings: list[tuple[StaticMember, Character, Character]],
    a_main_indexes: frozenset[int],
    *,
    mains_in_set: bool,
) -> SplitRosterRun:
    participants = []
    for index, (member, main, alt) in enumerate(bindings):
        use_main = (index in a_main_indexes) is mains_in_set
        character = main if use_main else alt
        participants.append(
            SplitRosterParticipant(
                roster_order=index + 1,
                static_member_id=member.id,
                static_member_name=member.display_name,
                character_id=character.id,
                character_name=character.name,
                world=character.world,
                job=character.job.abbreviation.upper(),
                job_name=character.job.name,
                combat_role=_combat_role(character.job.role),
                designation=CharacterKind.MAIN if use_main else CharacterKind.ALT,
            )
        )
    role_counts = SplitRoleCounts(
        tanks=sum(row.combat_role is CombatRole.TANK for row in participants),
        healers=sum(row.combat_role is CombatRole.HEALER for row in participants),
        dps=sum(row.combat_role is CombatRole.DPS for row in participants),
    )
    return SplitRosterRun(name, tuple(participants), role_counts)


def _validate_split_run(
    ordinal: int, identifier: str, run: SplitRosterRun
) -> list[SplitCandidateRejection]:
    rejections: list[SplitCandidateRejection] = []

    def reject(code: SplitRejectionCode, message: str) -> None:
        rejections.append(
            SplitCandidateRejection(
                ordinal,
                identifier,
                run.name,
                code,
                message,
                run.role_counts,
            )
        )

    if len(run.participants) != 8:
        reject(
            SplitRejectionCode.INVALID_PARTICIPANT_COUNT, "Run must contain exactly 8 participants."
        )
    if len({row.character_id for row in run.participants}) != 8:
        reject(
            SplitRejectionCode.DUPLICATE_CHARACTER, "A character appears more than once in the run."
        )
    if len({row.static_member_id for row in run.participants}) != 8:
        reject(
            SplitRejectionCode.DUPLICATE_MEMBER,
            "A static member appears more than once in the run.",
        )
    mains = sum(row.designation is CharacterKind.MAIN for row in run.participants)
    alts = sum(row.designation is CharacterKind.ALT for row in run.participants)
    if (mains, alts) != (4, 4):
        reject(
            SplitRejectionCode.INVALID_DESIGNATION_COUNT,
            f"Run has {mains} Mains and {alts} Alts; exactly 4 of each are required.",
        )
    if run.role_counts != SplitRoleCounts(2, 2, 4):
        reject(
            SplitRejectionCode.INVALID_ROLE_COMPOSITION,
            f"Run has {run.role_counts.tanks} Tanks, {run.role_counts.healers} Healers, "
            f"and {run.role_counts.dps} DPS; exactly 2/2/4 are required.",
        )
    return rejections


def _validate_complement(
    ordinal: int,
    identifier: str,
    run_a: SplitRosterRun,
    run_b: SplitRosterRun,
) -> list[SplitCandidateRejection]:
    a = {row.static_member_id: row for row in run_a.participants}
    b = {row.static_member_id: row for row in run_b.participants}
    complementary = (
        len(a) == len(b) == 8
        and set(a) == set(b)
        and all(
            a[member_id].designation is not b[member_id].designation
            and a[member_id].character_id != b[member_id].character_id
            for member_id in a
        )
    )
    if complementary:
        return []
    return [
        SplitCandidateRejection(
            ordinal,
            identifier,
            None,
            SplitRejectionCode.NON_COMPLEMENTARY_RUNS,
            "Run A and Run B are not exact Main/Alt complements.",
        )
    ]


def _validate_roster(static: Static, issues: list[LootPlanningIssue]) -> list[Character]:
    members = sorted((member for member in static.members if member.active), key=lambda row: row.id)
    if len(members) != 8:
        issues.append(
            _error(
                LootPlanningIssueCode.INVALID_MEMBER_COUNT,
                f"Exactly eight active static members are required; found {len(members)}.",
            )
        )
    mains: list[Character] = []
    for member in members:
        active_mains = sorted(
            (
                character
                for character in member.characters
                if character.active and character.kind is CharacterKind.MAIN
            ),
            key=lambda row: row.id,
        )
        if not active_mains:
            issues.append(
                _error(
                    LootPlanningIssueCode.MISSING_MAIN,
                    f"{member.display_name} has no active Main character.",
                )
            )
            continue
        if len(active_mains) > 1:
            issues.append(
                _error(
                    LootPlanningIssueCode.DUPLICATE_MAIN,
                    f"{member.display_name} has more than one active Main character.",
                )
            )
            continue
        main = active_mains[0]
        if main.static_member_id != member.id:
            issues.append(
                _error(
                    LootPlanningIssueCode.INVALID_MAIN_BINDING,
                    f"{main.name} does not belong to {member.display_name}.",
                )
            )
            continue
        job = main.job.abbreviation.upper()
        if not is_supported_regular_job(job):
            issues.append(
                _warning(
                    LootPlanningIssueCode.UNSUPPORTED_JOB,
                    f"{main.name} has unsupported job {job}; it is ranked after supported jobs.",
                )
            )
        mains.append(main)
    return mains


def _validate_tracked_loot(
    tier: RaidTier, issues: list[LootPlanningIssue]
) -> dict[RegularTrackedDrop, tuple[RaidFloor, FloorLootRule]]:
    floors = {floor.floor_number: floor for floor in tier.floors}
    configured: dict[RegularTrackedDrop, tuple[RaidFloor, FloorLootRule]] = {}
    for drop in REGULAR_TRACKED_DROPS:
        floor = floors.get(drop.floor_number)
        matches = (
            [rule for rule in floor.loot_rules if rule.loot_type.code == drop.loot_type_code]
            if floor is not None
            else []
        )
        rule = matches[0] if len(matches) == 1 else None
        material_matches = drop.material_code is None or (
            rule is not None
            and rule.augmentation_material_type is not None
            and rule.augmentation_material_type.code == drop.material_code
        )
        if (
            floor is None
            or rule is None
            or rule.expected_quantity != 1
            or rule.loot_type.category is not drop.category
            or not material_matches
        ):
            issues.append(
                _error(
                    LootPlanningIssueCode.MISSING_LOOT_CONFIGURATION,
                    f"{tier.name} does not provide exactly one configured {drop.label} "
                    f"on Floor {drop.floor_number}.",
                )
            )
            continue
        configured[drop] = (floor, rule)
    return configured


def _validate_needs(
    mains: list[Character],
    tier: RaidTier,
    needs: dict[int, CharacterNeedsResult],
    issues: list[LootPlanningIssue],
) -> None:
    for character in mains:
        result = needs[character.id]
        if result.selected_bis_set is None:
            issues.append(
                _error(
                    LootPlanningIssueCode.MISSING_BIS,
                    f"{character.name} has no selected BiS set for {tier.name}.",
                )
            )
            continue
        if result.selected_bis_set.raid_tier_id != tier.id:
            issues.append(
                _error(
                    LootPlanningIssueCode.CROSS_TIER_BIS,
                    f"{character.name}'s selected BiS set belongs to another raid tier.",
                )
            )
        if result.configuration_warnings:
            issues.extend(
                _error(
                    LootPlanningIssueCode.INVALID_BIS_CONFIGURATION,
                    f"{character.name}: {message}",
                )
                for message in result.configuration_warnings
            )


def _confirmed_grant_counts(
    session: Session, tier_id: int, character_ids: tuple[int, ...]
) -> Counter[tuple[int, int]]:
    rows = session.execute(
        select(
            ConfirmedReclearMaterialGrant.character_id,
            ConfirmedReclearMaterialGrant.augmentation_material_type_id,
            func.count(),
        )
        .join(ConfirmedReclearMaterialGrant.augmentation_material_type)
        .where(
            ConfirmedReclearMaterialGrant.character_id.in_(character_ids),
            ConfirmedReclearMaterialGrant.augmentation_material_type.has(raid_tier_id=tier_id),
        )
        .group_by(
            ConfirmedReclearMaterialGrant.character_id,
            ConfirmedReclearMaterialGrant.augmentation_material_type_id,
        )
    )
    return Counter(
        {(character_id, material_id): count for character_id, material_id, count in rows}
    )


def _assign_drop(
    drop: RegularTrackedDrop,
    configured: tuple[RaidFloor, FloorLootRule],
    mains: list[Character],
    needs: dict[int, CharacterNeedsResult],
    participants: dict[int, RegularPlanParticipant],
    grant_counts: Counter[tuple[int, int]],
) -> RegularLootAssignment:
    floor, rule = configured
    if drop.material_code is not None:
        return _assign_material(drop, floor, rule, mains, needs, participants, grant_counts)
    eligible = [
        character
        for character in mains
        if any(
            row.status is NeedStatus.NEEDS_SAVAGE_DROP
            and row.required_raid_floor is not None
            and row.required_raid_floor.floor_number == drop.floor_number
            and row.required_loot_type is not None
            and row.required_loot_type.code == drop.loot_type_code
            for row in needs[character.id].slot_results
        )
    ]
    winner = min(eligible, key=_character_rank) if eligible else None
    if winner is None:
        return _free_roll(floor, drop, f"No participating Main needs the {drop.label}.")
    participant = participants[winner.id]
    return RegularLootAssignment(
        floor.floor_number,
        floor.name,
        drop.label,
        PlannedLootDisposition.ASSIGNED,
        participant,
        participant.job,
        CharacterKind.MAIN,
        f"{winner.name} is the highest-priority participating Main who still needs this drop.",
        intended_bis_set_item_id=_planned_bis_item_id(needs[winner.id], floor, drop.loot_type_code),
        intended_final_item_id=_planned_bis_item_final_id(
            needs[winner.id], floor, drop.loot_type_code
        ),
    )


def _planned_bis_item_id(result, floor, loot_type_code):
    item = _planned_bis_item_row(result, floor, loot_type_code)
    return item.id if item is not None else None


def _planned_bis_item_final_id(result, floor, loot_type_code):
    item = _planned_bis_item_row(result, floor, loot_type_code)
    return item.desired_item_id if item is not None else None


def _planned_bis_item_row(result, floor, loot_type_code):
    return next(
        (
            next(item for item in need.bis_set.items if item.gear_slot_id == need.slot.id)
            for need in result.slot_results
            if need.status is NeedStatus.NEEDS_SAVAGE_DROP
            and need.required_raid_floor is not None
            and need.required_raid_floor.id == floor.id
            and need.required_loot_type is not None
            and need.required_loot_type.code == loot_type_code
        ),
        None,
    )


def _assign_material(
    drop: RegularTrackedDrop,
    floor: RaidFloor,
    rule: FloorLootRule,
    mains: list[Character],
    needs: dict[int, CharacterNeedsResult],
    participants: dict[int, RegularPlanParticipant],
    grant_counts: Counter[tuple[int, int]],
) -> RegularLootAssignment:
    material = rule.augmentation_material_type
    remaining = {
        character.id: next(
            (
                need.additional_units_needed
                for need in needs[character.id].augmentation_needs
                if need.material.id == material.id
            ),
            0,
        )
        for character in mains
    }
    eligible = [character for character in mains if remaining[character.id] > 0]
    winner = (
        min(
            eligible,
            key=lambda character: (
                grant_counts[(character.id, material.id)],
                -remaining[character.id],
                *_character_rank(character),
            ),
        )
        if eligible
        else None
    )
    if winner is None:
        return _free_roll(
            floor,
            drop,
            f"No participating Main has remaining need for {drop.label}.",
        )
    participant = participants[winner.id]
    grants = grant_counts[(winner.id, material.id)]
    need = remaining[winner.id]
    return RegularLootAssignment(
        floor.floor_number,
        floor.name,
        drop.label,
        PlannedLootDisposition.ASSIGNED,
        participant,
        participant.job,
        CharacterKind.MAIN,
        f"{winner.name} has {grants} confirmed {drop.label} reclear grants and "
        f"{need} remaining {drop.label} need.",
        MaterialFairnessContext(drop.label, grants, need),
    )


def _character_rank(character: Character) -> tuple[int, int, int]:
    return (
        regular_job_priority_rank(character.job.abbreviation),
        character.static_member_id,
        character.id,
    )


def _free_roll(
    floor: RaidFloor, drop: RegularTrackedDrop, explanation: str
) -> RegularLootAssignment:
    return RegularLootAssignment(
        floor.floor_number,
        floor.name,
        drop.label,
        PlannedLootDisposition.FREE_ROLL,
        None,
        None,
        None,
        explanation,
    )


def _invalid(
    static: PlanningStatic | None,
    tier: PlanningRaidTier | None,
    issues: list[LootPlanningIssue],
) -> RegularLootPlanResult:
    return RegularLootPlanResult(static, tier, None, issues=tuple(issues))


def _invalid_split(
    static: PlanningStatic | None,
    tier: PlanningRaidTier | None,
    issues: list[LootPlanningIssue],
) -> SplitRosterCandidatesResult:
    return SplitRosterCandidatesResult(static, tier, None, issues=tuple(issues))


def _target_week(session: Session, static_id: int) -> int:
    completed = session.scalar(
        select(func.count())
        .select_from(ReclearWeek)
        .where(
            ReclearWeek.static_id == static_id,
            ReclearWeek.workflow_state == ReclearWorkflowState.CLOSED,
        )
    )
    return 2 + (completed or 0)


def _error(code: LootPlanningIssueCode, message: str) -> LootPlanningIssue:
    return LootPlanningIssue(code, LootPlanningIssueSeverity.ERROR, message)


def _warning(code: LootPlanningIssueCode, message: str) -> LootPlanningIssue:
    return LootPlanningIssue(code, LootPlanningIssueSeverity.WARNING, message)
