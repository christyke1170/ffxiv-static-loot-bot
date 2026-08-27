"""Regular/split roster validation and fictional weekly plan tests."""

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AugmentationMaterialType,
    BisSet,
    BisSetItem,
    Character,
    CharacterAugmentationInventory,
    CharacterBisSelection,
    CharacterKind,
    ClearMode,
    DiscordGuild,
    FloorLootRule,
    GearClassification,
    GearSlot,
    GearSlotCode,
    InventoryItem,
    Item,
    Job,
    LootAssignment,
    LootAssignmentState,
    LootCategory,
    LootPlan,
    LootReceipt,
    LootType,
    RaidFloor,
    RaidTier,
    ReclearGroup,
    ReclearParticipant,
    ReclearWeek,
    Static,
    StaticMember,
    WeeklyHierarchySnapshotEntry,
    WeeklyLockout,
)
from app.schemas.planning import LootPlanGenerationError, ValidationIssueCode
from app.services import generate_weekly_loot_plan, seed_reference_data, validate_weekly_roster


class PlanningFixture:
    def __init__(self, session: Session, mode: ClearMode = ClearMode.REGULAR) -> None:
        self.session = session
        seed_reference_data(session)
        self.slots = {slot.code: slot for slot in session.scalars(select(GearSlot))}
        self.guild = DiscordGuild(discord_guild_id=880001, name="Planning Guild")
        self.static = Static(guild=self.guild, name="Planning Static")
        self.tier = RaidTier(code="PLAN", name="Fictional Planning Tier")
        self.floor = RaidFloor(raid_tier=self.tier, floor_number=1, name="Fictional One")
        self.coffer_item = Item(name="Fictional Planning Coffer")
        self.coffer = LootType(
            raid_tier=self.tier,
            code="HEAD_COFFER",
            name="Fictional Head Coffer",
            category=LootCategory.COFFER,
            item=self.coffer_item,
        )
        self.rule = FloorLootRule(raid_floor=self.floor, loot_type=self.coffer, expected_quantity=1)
        self.week = ReclearWeek(
            static=self.static,
            raid_tier=self.tier,
            week_start=date(2026, 8, 25),
            clear_mode=mode,
        )
        count = 1 if mode is ClearMode.REGULAR else 2
        self.groups = [
            ReclearGroup(reclear_week=self.week, group_number=number)
            for number in range(1, count + 1)
        ]
        self.members: list[StaticMember] = []
        self.mains: list[Character] = []
        self.alts: list[Character] = []
        self.jobs: list[Job] = []
        for index in range(8):
            member = StaticMember(
                static=self.static,
                discord_user_id=880100 + index,
                display_name=f"Member {index + 1}",
            )
            job = Job(abbreviation=f"P{index + 1}", name=f"Planning Job {index + 1}")
            alt_job = Job(abbreviation=f"A{index + 1}", name=f"Alt Job {index + 1}")
            main = Character(
                static_member=member,
                job=job,
                name=f"Main {index + 1}",
                world="Fictional",
                kind=CharacterKind.MAIN,
            )
            alt = Character(
                static_member=member,
                job=alt_job,
                name=f"Alt {index + 1}",
                world="Fictional",
                kind=CharacterKind.ALT,
            )
            self.members.append(member)
            self.mains.append(main)
            self.alts.append(alt)
            self.jobs.append(job)
        session.add(self.week)
        session.flush()
        if mode is ClearMode.REGULAR:
            self.set_group(0, self.mains)
        else:
            self.set_group(0, self.mains[:4] + self.alts[4:])
            self.set_group(1, self.alts[:4] + self.mains[4:])
        self.set_hierarchy(self.jobs)
        session.commit()

    def set_group(self, index: int, characters: list[Character]) -> None:
        group = self.groups[index]
        for participant in list(group.participants):
            self.session.delete(participant)
        self.session.flush()
        self.session.expire(group, ["participants"])
        rows = [
            ReclearParticipant(reclear_week=self.week, group=group, character=character)
            for character in characters
        ]
        self.session.add_all(rows)
        self.session.flush()

    def set_hierarchy(self, jobs: list[Job]) -> None:
        for entry in list(self.week.hierarchy_snapshot):
            self.session.delete(entry)
        self.session.flush()
        self.session.expire(self.week, ["hierarchy_snapshot"])
        self.week.hierarchy_snapshot.extend(
            [
                WeeklyHierarchySnapshotEntry(
                    job=job, position=index, job_abbreviation=job.abbreviation
                )
                for index, job in enumerate(jobs, 1)
            ]
        )

    def select_bis(
        self,
        character: Character,
        *,
        job: Job | None = None,
        slots: tuple[GearSlotCode, ...] = (GearSlotCode.HEAD,),
    ) -> BisSet:
        bis_set = BisSet(job=job or character.job, raid_tier=self.tier, name=character.name)
        for code, slot in self.slots.items():
            if code in slots:
                bis_set.items.append(
                    BisSetItem(
                        gear_slot=slot,
                        classification=GearClassification.SAVAGE,
                        raid_floor=self.floor,
                        loot_type=self.coffer,
                    )
                )
            else:
                bis_set.items.append(
                    BisSetItem(gear_slot=slot, classification=GearClassification.NOT_APPLICABLE)
                )
        self.session.add(
            CharacterBisSelection(character=character, raid_tier=self.tier, bis_set=bis_set)
        )
        self.session.commit()
        return bis_set


@pytest.fixture
def regular(session: Session) -> PlanningFixture:
    return PlanningFixture(session)


@pytest.fixture
def split(session: Session) -> PlanningFixture:
    return PlanningFixture(session, ClearMode.SPLIT)


def issue_codes(result) -> set[ValidationIssueCode]:
    return {issue.code for issue in result.issues}


def test_valid_regular_eight_main_group(regular: PlanningFixture) -> None:
    assert validate_weekly_roster(regular.session, regular.week.id).is_valid


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("alt", ValidationIssueCode.ALT_COUNT),
        ("missing", ValidationIssueCode.MISSING_MEMBER),
        ("duplicate_member", ValidationIssueCode.DUPLICATE_MEMBER),
    ],
)
def test_regular_roster_errors_are_collected(
    regular: PlanningFixture, mutation: str, expected: ValidationIssueCode
) -> None:
    characters = list(regular.mains)
    if mutation == "alt":
        characters[0] = regular.alts[0]
    elif mutation == "missing":
        characters.pop()
    else:
        extra = Character(
            static_member=regular.members[0],
            job=regular.jobs[0],
            name="Second Main",
            world="Fictional",
            kind=CharacterKind.MAIN,
        )
        regular.session.add(extra)
        regular.session.flush()
        characters[-1] = extra
    regular.set_group(0, characters)
    regular.session.commit()
    assert expected in issue_codes(validate_weekly_roster(regular.session, regular.week.id))


def test_regular_floor_lockout(regular: PlanningFixture) -> None:
    regular.session.add(
        WeeklyLockout(
            character=regular.mains[0],
            raid_floor=regular.floor,
            week_start=regular.week.week_start,
            cleared=True,
            loot_eligible=False,
        )
    )
    regular.session.commit()
    result = validate_weekly_roster(regular.session, regular.week.id)
    assert ValidationIssueCode.FLOOR_LOCKOUT in issue_codes(result)


def test_valid_split_has_four_mains_four_alts_and_opposite_pairs(
    split: PlanningFixture,
) -> None:
    result = validate_weekly_roster(split.session, split.week.id)
    assert result.is_valid
    assert all(
        sum(row.character.kind is CharacterKind.MAIN for row in group.participants) == 4
        for group in split.groups
    )


@pytest.mark.parametrize("mutation", ["same_group", "missing_alt", "extra_main"])
def test_split_invalid_membership(split: PlanningFixture, mutation: str) -> None:
    if mutation == "same_group":
        first = list(split.mains[:4] + split.alts[4:])
        second = list(split.alts[:4] + split.mains[4:])
        first[-1], second[0] = second[0], first[-1]
        for group in split.groups:
            for participant in list(group.participants):
                split.session.delete(participant)
        split.session.flush()
        for group in split.groups:
            split.session.expire(group, ["participants"])
        split.set_group(0, first)
        split.set_group(1, second)
        expected = ValidationIssueCode.MAIN_ALT_NOT_OPPOSITE
        split.session.commit()
        codes = issue_codes(validate_weekly_roster(split.session, split.week.id))
        assert expected in codes
        return
    characters = list(split.mains[:4] + split.alts[4:])
    if mutation == "missing_alt":
        characters.pop()
        expected = ValidationIssueCode.MISSING_ALT
    else:
        kind = CharacterKind.MAIN
        extra = Character(
            static_member=split.members[0],
            job=split.jobs[0],
            name=f"Extra {mutation}",
            world="Fictional",
            kind=kind,
        )
        split.session.add(extra)
        characters[-1] = extra
        expected = ValidationIssueCode.MAIN_COUNT
    split.set_group(0, characters)
    split.session.commit()
    codes = issue_codes(validate_weekly_roster(split.session, split.week.id))
    assert expected in codes
    assert ValidationIssueCode.MEMBER_NOT_IN_BOTH_GROUPS in codes


def test_split_lockout_identifies_only_configured_group(split: PlanningFixture) -> None:
    split.session.add(
        WeeklyLockout(
            character=split.mains[0],
            raid_floor=split.floor,
            week_start=split.week.week_start,
            loot_eligible=False,
        )
    )
    split.session.commit()
    lockouts = [
        issue
        for issue in validate_weekly_roster(split.session, split.week.id).issues
        if issue.code is ValidationIssueCode.FLOOR_LOCKOUT
    ]
    assert [issue.group_id for issue in lockouts] == [split.groups[0].id]


def test_hierarchy_wins_and_uses_selected_bis_job(regular: PlanningFixture) -> None:
    selected_job = Job(abbreviation="TOP", name="Selected Top Job")
    regular.session.add(selected_job)
    regular.session.flush()
    regular.set_hierarchy([selected_job, *regular.jobs])
    regular.select_bis(regular.mains[0], job=selected_job)
    regular.select_bis(regular.mains[1])
    regular.session.commit()
    result = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert result.assignments[0].intended_recipient is regular.mains[0]
    assert result.assignments[0].hierarchy_position == 1


def test_missing_hierarchy_job_warns_and_sorts_last(regular: PlanningFixture) -> None:
    regular.set_hierarchy(regular.jobs[1:])
    regular.select_bis(regular.mains[0])
    regular.select_bis(regular.mains[1])
    regular.session.commit()
    result = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert result.assignments[0].intended_recipient is regular.mains[1]
    assert [warning.code for warning in result.warnings] == ["MISSING_HIERARCHY_JOB"]


def test_hierarchy_beats_prior_receipt_balancing(regular: PlanningFixture) -> None:
    regular.select_bis(regular.mains[0])
    regular.select_bis(regular.mains[1])
    prior_week = ReclearWeek(
        static=regular.static,
        raid_tier=regular.tier,
        week_start=date(2026, 8, 18),
        clear_mode=ClearMode.REGULAR,
    )
    prior_plan = LootPlan(reclear_week=prior_week, name="Prior")
    prior_assignment = LootAssignment(
        loot_plan=prior_plan,
        raid_floor=regular.floor,
        loot_type=regular.coffer,
        intended_character=regular.mains[0],
    )
    prior_assignment.receipt = LootReceipt(quantity=10)
    regular.session.add(prior_plan)
    regular.session.commit()
    result = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert result.assignments[0].intended_recipient is regular.mains[0]


def test_snapshot_priority_is_not_changed_by_other_hierarchy_data(
    regular: PlanningFixture,
) -> None:
    regular.select_bis(regular.mains[0])
    regular.select_bis(regular.mains[1])
    regular.jobs.reverse()
    result = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert result.assignments[0].intended_recipient is regular.mains[0]


def test_regular_expected_quantity_simulates_slots_and_records_backup(
    regular: PlanningFixture,
) -> None:
    regular.rule.expected_quantity = 2
    regular.select_bis(regular.mains[0])
    regular.select_bis(regular.mains[1])
    result = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert [row.drop_instance_number for row in result.assignments] == [1, 2]
    assert [row.intended_recipient for row in result.assignments] == regular.mains[:2]
    assert result.assignments[0].backup_recipient is regular.mains[1]


def test_one_character_can_receive_multiple_distinct_needed_slots(
    regular: PlanningFixture,
) -> None:
    regular.rule.expected_quantity = 2
    regular.select_bis(regular.mains[0], slots=(GearSlotCode.HEAD, GearSlotCode.BODY))
    result = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert [row.intended_bis_slot.code for row in result.assignments] == [
        GearSlotCode.HEAD,
        GearSlotCode.BODY,
    ]


def test_one_pld_weapon_coffer_creates_one_bundled_assignment(
    regular: PlanningFixture,
) -> None:
    pld = regular.mains[0]
    pld.job = regular.session.scalar(select(Job).where(Job.abbreviation == "PLD"))
    regular.coffer.code = "WEAPON_COFFER"
    regular.coffer.name = "Fictional Weapon Coffer"
    bis_set = BisSet(job=pld.job, raid_tier=regular.tier, name="PLD Weapon Bundle")
    for code, slot in regular.slots.items():
        if code in {GearSlotCode.WEAPON, GearSlotCode.OFFHAND}:
            bis_set.items.append(
                BisSetItem(
                    gear_slot=slot,
                    classification=GearClassification.SAVAGE,
                    raid_floor=regular.floor,
                    loot_type=regular.coffer,
                )
            )
        else:
            bis_set.items.append(
                BisSetItem(gear_slot=slot, classification=GearClassification.NOT_APPLICABLE)
            )
    regular.session.add(
        CharacterBisSelection(character=pld, raid_tier=regular.tier, bis_set=bis_set)
    )
    regular.session.commit()

    result = generate_weekly_loot_plan(regular.session, regular.week.id)

    assigned = [row.assignment for row in result.assignments if row.intended_recipient is pld]
    assert len(assigned) == 1
    assert {row.bis_set_item.gear_slot.code for row in assigned[0].completion_items} == {
        GearSlotCode.WEAPON,
        GearSlotCode.OFFHAND,
    }


def test_owned_matching_coffer_and_no_need_become_leftovers(regular: PlanningFixture) -> None:
    regular.select_bis(regular.mains[0])
    regular.session.add(
        InventoryItem(character=regular.mains[0], loot_type=regular.coffer, quantity=1)
    )
    regular.session.commit()
    result = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert result.assignments[0].state is LootAssignmentState.LEFTOVER
    assert result.assignments[0].intended_recipient is None


def test_augmentation_additional_need_and_simulation_are_respected(
    regular: PlanningFixture,
) -> None:
    material = AugmentationMaterialType(
        raid_tier=regular.tier,
        code="POLISH",
        name="Fictional Polish",
        item=Item(name="Fictional Polish"),
    )
    material_loot = LootType(
        raid_tier=regular.tier,
        code="POLISH",
        name="Fictional Polish",
        category=LootCategory.AUGMENTATION_MATERIAL,
        item=material.item,
    )
    regular.rule.expected_quantity = 0
    regular.floor.loot_rules.append(
        FloorLootRule(
            loot_type=material_loot,
            augmentation_material_type=material,
            expected_quantity=2,
        )
    )
    bis_set = BisSet(job=regular.jobs[0], raid_tier=regular.tier, name="Augmentation Set")
    for code, slot in regular.slots.items():
        if code is GearSlotCode.BODY:
            bis_set.items.append(
                BisSetItem(
                    gear_slot=slot,
                    classification=GearClassification.AUGMENTED_TOME,
                    augmentation_material_type=material,
                )
            )
        else:
            bis_set.items.append(
                BisSetItem(gear_slot=slot, classification=GearClassification.NOT_APPLICABLE)
            )
    regular.session.add(
        CharacterBisSelection(character=regular.mains[0], raid_tier=regular.tier, bis_set=bis_set)
    )
    regular.session.commit()
    result = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert [row.state for row in result.assignments] == [
        LootAssignmentState.PROPOSED,
        LootAssignmentState.LEFTOVER,
    ]
    assert result.assignments[0].recipient_owns_required_base_tome_item is False
    assert "not yet owned" in result.assignments[0].reason


def test_split_generates_group_isolated_drops_for_mains_only(split: PlanningFixture) -> None:
    split.select_bis(split.mains[0])
    split.select_bis(split.mains[4])
    result = generate_weekly_loot_plan(split.session, split.week.id)
    assert len(result.assignments) == 2
    assert [row.group for row in result.assignments] == split.groups
    assert all(row.intended_recipient.kind is CharacterKind.MAIN for row in result.assignments)


def test_repeated_generation_is_idempotent_and_preserves_manual_state(
    regular: PlanningFixture,
) -> None:
    regular.select_bis(regular.mains[0])
    first = generate_weekly_loot_plan(regular.session, regular.week.id)
    first.assignments[0].assignment.manually_overridden = True
    first.assignments[0].assignment.state = LootAssignmentState.CONFIRMED
    regular.session.commit()
    second = generate_weekly_loot_plan(regular.session, regular.week.id)
    assert second.reused_existing_plan
    assert second.assignments[0].state is LootAssignmentState.CONFIRMED
    assert regular.session.scalar(select(func.count()).select_from(LootAssignment)) == 1


def test_invalid_roster_creates_no_plan(regular: PlanningFixture) -> None:
    regular.session.delete(regular.groups[0].participants[-1])
    regular.session.commit()
    regular.session.expire(regular.groups[0], ["participants"])
    with pytest.raises(LootPlanGenerationError) as error:
        generate_weekly_loot_plan(regular.session, regular.week.id)
    assert error.value.validation is not None
    assert regular.session.scalar(select(func.count()).select_from(LootAssignment)) == 0


def test_planning_does_not_change_inventory_or_materials(regular: PlanningFixture) -> None:
    regular.select_bis(regular.mains[0])
    material_count = regular.session.scalar(
        select(func.count()).select_from(CharacterAugmentationInventory)
    )
    inventory_count = regular.session.scalar(select(func.count()).select_from(InventoryItem))
    generate_weekly_loot_plan(regular.session, regular.week.id)
    assert (
        regular.session.scalar(select(func.count()).select_from(InventoryItem)) == inventory_count
    )
    assert (
        regular.session.scalar(select(func.count()).select_from(CharacterAugmentationInventory))
        == material_count
    )
