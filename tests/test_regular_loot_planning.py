"""Pure Regular-reclear loot planning tests using a fictional four-floor tier."""

from dataclasses import asdict
from datetime import date, timedelta

import pytest
from sqlalchemy import event, func, select, text

from app.loot_planning_config import REGULAR_JOB_PRIORITY, regular_job_priority_rank
from app.models import (
    AuditLog,
    AugmentationMaterialType,
    BisSet,
    BisSetItem,
    Character,
    CharacterAugmentationInventory,
    CharacterBisSelection,
    CharacterFloorBookBalance,
    CharacterGearSlot,
    CharacterKind,
    ClearMode,
    ConfirmedReclearMaterialGrant,
    DiscordGuild,
    FloorLootRule,
    GearClassification,
    GearSlot,
    GearSlotCode,
    Item,
    Job,
    LootAssignment,
    LootCategory,
    LootPlan,
    LootType,
    PlannedLootDisposition,
    RaidFloor,
    RaidTier,
    ReclearWeek,
    ReclearWorkflowState,
    Static,
    StaticMember,
)
from app.schemas.loot_planning import LootPlanningIssueCode
from app.services import calculate_regular_loot_plan, seed_reference_data

DROP_CONFIG = (
    (1, "EARRING_COFFER", "Earring Coffer", GearSlotCode.EARRINGS, None),
    (1, "NECKLACE_COFFER", "Necklace Coffer", GearSlotCode.NECKLACE, None),
    (1, "BRACELET_COFFER", "Bracelet Coffer", GearSlotCode.BRACELETS, None),
    (1, "RING_COFFER", "Ring Coffer", GearSlotCode.RING_1, None),
    (2, "HEAD_COFFER", "Head Coffer", GearSlotCode.HEAD, None),
    (2, "GLOVES_COFFER", "Gloves Coffer", GearSlotCode.HANDS, None),
    (2, "BOOTS_COFFER", "Boots Coffer", GearSlotCode.FEET, None),
    (2, "ACCESSORY_GLAZE", "Glaze", None, "ACCESSORY_GLAZE"),
    (3, "CHEST_COFFER", "Chest Coffer", GearSlotCode.BODY, None),
    (3, "PANTS_COFFER", "Pants Coffer", GearSlotCode.LEGS, None),
    (3, "ARMOR_TWINE", "Twine", None, "ARMOR_TWINE"),
    (4, "WEAPON_COFFER", "Weapon Coffer", GearSlotCode.WEAPON, None),
)


class RegularFixture:
    def __init__(self, session) -> None:
        self.session = session
        seed_reference_data(session)
        self.slots = {row.code: row for row in session.scalars(select(GearSlot))}
        self.guild = DiscordGuild(discord_guild_id=771001, name="Regular Guild")
        self.tier = RaidTier(code="REGULAR_ENGINE", name="Fictional Regular Tier")
        self.static = Static(
            guild=self.guild,
            name="Regular Static",
            active_raid_tier=self.tier,
        )
        self.floors = {
            number: RaidFloor(
                raid_tier=self.tier,
                floor_number=number,
                name=f"Fictional Floor {number}",
            )
            for number in range(1, 5)
        }
        self.materials = {
            code: AugmentationMaterialType(
                raid_tier=self.tier,
                code=code,
                name="Fictional Glaze" if "GLAZE" in code else "Fictional Twine",
                item=Item(name=f"Fictional {code} Item"),
            )
            for code in ("ACCESSORY_GLAZE", "ARMOR_TWINE")
        }
        self.loot_types: dict[str, LootType] = {}
        for floor_number, code, label, _slot, material_code in DROP_CONFIG:
            loot_type = LootType(
                raid_tier=self.tier,
                code=code,
                name=f"Fictional {label}",
                category=(
                    LootCategory.AUGMENTATION_MATERIAL if material_code else LootCategory.COFFER
                ),
                item=Item(name=f"Fictional {label} Item"),
            )
            self.loot_types[code] = loot_type
            self.floors[floor_number].loot_rules.append(
                FloorLootRule(
                    loot_type=loot_type,
                    expected_quantity=1,
                    augmentation_material_type=self.materials.get(material_code),
                )
            )
        canonical_jobs = {
            row.abbreviation: row
            for row in self.session.scalars(
                select(Job).where(
                    Job.abbreviation.in_(("WAR", "PLD", "GNB", "DRK", "SCH", "AST", "SGE", "SAM"))
                )
            )
        }
        self.mains: list[Character] = []
        self.alts: list[Character] = []
        self.selections: dict[int, CharacterBisSelection] = {}
        jobs = ("WAR", "PLD", "GNB", "DRK", "SCH", "AST", "SGE", "SAM")
        for index, abbreviation in enumerate(jobs, 1):
            member = StaticMember(
                static=self.static,
                discord_user_id=771100 + index,
                display_name=f"Member {index}",
            )
            job = canonical_jobs[abbreviation]
            main = Character(
                static_member=member,
                job=job,
                name=f"Main {index}",
                world="Fictional",
                kind=CharacterKind.MAIN,
            )
            alt = Character(
                static_member=member,
                job=Job(
                    abbreviation=f"A{index}",
                    name=f"Fictional Alt Job {index}",
                    role="Test",
                ),
                name=f"Alt {index}",
                world="Fictional",
                kind=CharacterKind.ALT,
            )
            bis_set = BisSet(job=job, raid_tier=self.tier, name=f"{main.name} BiS")
            for slot in self.slots.values():
                config = next((row for row in DROP_CONFIG if row[3] is slot.code), None)
                if abbreviation == "PLD" and slot.code is GearSlotCode.OFFHAND:
                    config = next(row for row in DROP_CONFIG if row[3] is GearSlotCode.WEAPON)
                bis_set.items.append(
                    BisSetItem(
                        gear_slot=slot,
                        classification=(
                            GearClassification.SAVAGE
                            if config
                            else GearClassification.NOT_APPLICABLE
                        ),
                        raid_floor=self.floors[config[0]] if config else None,
                        loot_type=self.loot_types[config[1]] if config else None,
                        book_cost=8
                        if config and slot.code is GearSlotCode.WEAPON
                        else 4
                        if config
                        else None,
                    )
                )
            selection = CharacterBisSelection(
                character=main,
                raid_tier=self.tier,
                bis_set=bis_set,
            )
            self.mains.append(main)
            self.alts.append(alt)
            self.session.add_all([main, alt, selection])
        self.session.commit()
        self._refresh_selections()

    def _refresh_selections(self) -> None:
        self.selections = {
            row.character_id: row for row in self.session.scalars(select(CharacterBisSelection))
        }

    def result(self):
        return calculate_regular_loot_plan(self.session, self.static.id)

    def requirement(self, character: Character, slot: GearSlotCode) -> BisSetItem:
        selection = self.selections[character.id]
        return next(row for row in selection.bis_set.items if row.gear_slot.code is slot)

    def set_augmented_need(
        self, character: Character, slot: GearSlotCode, material_code: str
    ) -> BisSetItem:
        requirement = self.requirement(character, slot)
        requirement.classification = GearClassification.AUGMENTED_TOME
        requirement.raid_floor = None
        requirement.loot_type = None
        requirement.book_cost = None
        requirement.augmentation_material_type = self.materials[material_code]
        self.session.commit()
        return requirement

    def complete_all_savage(self) -> None:
        for character in self.mains:
            for requirement in self.selections[character.id].bis_set.items:
                if requirement.classification is GearClassification.SAVAGE:
                    character.gear_slots.append(
                        CharacterGearSlot(
                            gear_slot=requirement.gear_slot,
                            current_classification=GearClassification.SAVAGE,
                        )
                    )
        self.session.commit()

    def grant(self, character: Character, material_code: str, count: int = 1) -> None:
        material = self.materials[material_code]
        floor = self.floors[2 if "GLAZE" in material_code else 3]
        loot_type = self.loot_types[material_code]
        existing_weeks = self.session.scalar(select(func.count()).select_from(ReclearWeek)) or 0
        for index in range(count):
            week = ReclearWeek(
                static=self.static,
                raid_tier=self.tier,
                week_start=date(2025, 1, 7) + timedelta(weeks=existing_weeks + index),
                clear_mode=ClearMode.REGULAR,
            )
            assignment = LootAssignment(
                loot_plan=LootPlan(
                    reclear_week=week, name=f"History {character.id}-{material_code}-{index}"
                ),
                raid_floor=floor,
                loot_type=loot_type,
                intended_character=character,
            )
            assignment.material_grant = ConfirmedReclearMaterialGrant(
                character=character,
                augmentation_material_type=material,
                confirmed_by_discord_user_id=771999,
            )
            self.session.add(assignment)
            self.session.flush()
        self.session.commit()


@pytest.fixture
def regular(session) -> RegularFixture:
    return RegularFixture(session)


def assignment(result, label: str):
    return next(row for row in result.run.assignments if row.loot_label == label)


def codes(result) -> set[LootPlanningIssueCode]:
    return {issue.code for issue in result.issues}


def test_job_priority_is_stable_and_unknown_jobs_sort_last() -> None:
    assert REGULAR_JOB_PRIORITY[0] == "SAM" and REGULAR_JOB_PRIORITY[-1] == "WAR"
    assert regular_job_priority_rank("sam") == 1
    assert regular_job_priority_rank("unknown") > regular_job_priority_rank("WAR")


def test_valid_regular_plan_has_fixed_drop_table_and_excludes_alts(regular: RegularFixture) -> None:
    result = regular.result()
    assert result.is_valid and result.mode is ClearMode.REGULAR
    assert result.run.name == "Regular"
    assert [row.character_name for row in result.run.participants] == [
        row.name for row in regular.mains
    ]
    assert all(row.designation is CharacterKind.MAIN for row in result.run.participants)
    assert not {row.name for row in regular.alts} & {
        row.character_name for row in result.run.participants
    }
    by_floor = {
        floor: [row.loot_label for row in result.run.assignments if row.floor_number == floor]
        for floor in range(1, 5)
    }
    assert by_floor == {
        1: ["Earring Coffer", "Necklace Coffer", "Bracelet Coffer", "Ring Coffer"],
        2: ["Head Coffer", "Gloves Coffer", "Boots Coffer", "Glaze"],
        3: ["Chest Coffer", "Pants Coffer", "Twine"],
        4: ["Weapon Coffer"],
    }
    labels = {row.loot_label for row in result.run.assignments}
    assert not labels & {
        "Weapon Tomestone",
        "Weapon Augment",
        "Random Savage Weapon",
        "Mount",
        "Music Roll",
        "Minion",
    }


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda fixture: setattr(fixture.static, "active", False),
            LootPlanningIssueCode.INACTIVE_STATIC,
        ),
        (
            lambda fixture: setattr(fixture.static, "active_raid_tier", None),
            LootPlanningIssueCode.MISSING_ACTIVE_TIER,
        ),
        (
            lambda fixture: setattr(fixture.mains[0].static_member, "active", False),
            LootPlanningIssueCode.INVALID_MEMBER_COUNT,
        ),
        (
            lambda fixture: setattr(fixture.mains[0], "active", False),
            LootPlanningIssueCode.MISSING_MAIN,
        ),
    ],
)
def test_invalid_static_or_roster_returns_no_assignments(regular, mutate, expected) -> None:
    mutate(regular)
    regular.session.commit()
    result = regular.result()
    assert not result.is_valid and result.run is None and expected in codes(result)


def test_duplicate_main_binding_is_invalid(regular: RegularFixture) -> None:
    member = regular.mains[0].static_member
    regular.session.add(
        Character(
            static_member=member,
            job=regular.mains[0].job,
            name="Duplicate Main",
            world="Fictional",
            kind=CharacterKind.MAIN,
        )
    )
    regular.session.commit()
    result = regular.result()
    assert not result.is_valid and LootPlanningIssueCode.DUPLICATE_MAIN in codes(result)


def test_missing_and_cross_tier_bis_are_invalid(regular: RegularFixture) -> None:
    selection = regular.selections[regular.mains[0].id]
    regular.session.delete(selection)
    regular.session.commit()
    missing = regular.result()
    assert not missing.is_valid and LootPlanningIssueCode.MISSING_BIS in codes(missing)

    other = RaidTier(code="OTHER_REGULAR", name="Other Regular Tier")
    regular.session.add(other)
    regular.session.commit()
    regular.session.execute(
        text(
            "INSERT INTO character_bis_selections "
            "(character_id, raid_tier_id, bis_set_id) VALUES (:character, :tier, :bis)"
        ),
        {"character": regular.mains[0].id, "tier": regular.tier.id, "bis": selection.bis_set_id},
    )
    regular.session.execute(
        text("UPDATE bis_sets SET raid_tier_id = :other WHERE id = :bis"),
        {"other": other.id, "bis": selection.bis_set_id},
    )
    regular.session.commit()
    regular.session.expire_all()
    crossed = regular.result()
    assert not crossed.is_valid
    assert LootPlanningIssueCode.CROSS_TIER_BIS in codes(crossed)


def test_unsupported_job_warns_and_sorts_after_supported_jobs(regular: RegularFixture) -> None:
    regular.mains[-1].job = Job(abbreviation="UNK", name="Unsupported Fictional Job")
    regular.session.commit()
    result = regular.result()
    assert result.is_valid and LootPlanningIssueCode.UNSUPPORTED_JOB in codes(result)
    assert assignment(result, "Head Coffer").recipient.character_name == "Main 7"


def test_missing_tier_loot_configuration_is_invalid(regular: RegularFixture) -> None:
    rule = next(
        row for row in regular.floors[2].loot_rules if row.loot_type.code == "GLOVES_COFFER"
    )
    regular.session.delete(rule)
    regular.session.commit()
    result = regular.result()
    assert not result.is_valid and result.run is None
    assert LootPlanningIssueCode.MISSING_LOOT_CONFIGURATION in codes(result)


def test_target_week_is_completed_week_plus_one(regular: RegularFixture) -> None:
    assert regular.result().target_week == 2
    regular.session.add(
        ReclearWeek(
            static=regular.static,
            raid_tier=regular.tier,
            week_start=date(2026, 8, 18),
            clear_mode=ClearMode.REGULAR,
            workflow_state=ReclearWorkflowState.CLOSED,
        )
    )
    regular.session.commit()
    assert regular.result().target_week == 3


def test_highest_priority_wins_ties_and_one_main_can_receive_many(regular: RegularFixture) -> None:
    result = regular.result()
    savage = [row for row in result.run.assignments if row.loot_label not in {"Twine", "Glaze"}]
    assert {row.recipient.character_name for row in savage} == {"Main 8"}


def test_same_job_tie_uses_static_roster_order(regular: RegularFixture) -> None:
    regular.mains[0].job = regular.mains[-1].job
    regular.session.commit()
    assert assignment(regular.result(), "Head Coffer").recipient.character_name == "Main 1"


@pytest.mark.parametrize("completion", ["equipped", "manual"])
def test_completed_savage_slot_is_ineligible(regular: RegularFixture, completion: str) -> None:
    winner = regular.mains[-1]
    requirement = regular.requirement(winner, GearSlotCode.HEAD)
    winner.gear_slots.append(
        CharacterGearSlot(
            gear_slot=requirement.gear_slot,
            current_classification=(
                GearClassification.GARBAGE if completion == "manual" else GearClassification.SAVAGE
            ),
            manually_complete=completion == "manual",
        )
    )
    regular.session.commit()
    assert assignment(regular.result(), "Head Coffer").recipient.character_name == "Main 7"


def test_not_applicable_is_ineligible(regular: RegularFixture) -> None:
    requirement = regular.requirement(regular.mains[-1], GearSlotCode.HEAD)
    requirement.classification = GearClassification.NOT_APPLICABLE
    requirement.raid_floor = None
    requirement.raid_floor_id = None
    requirement.loot_type = None
    requirement.loot_type_id = None
    requirement.book_cost = None
    regular.session.commit()
    assert assignment(regular.result(), "Head Coffer").recipient.character_name == "Main 7"


def test_item_level_and_books_do_not_remove_savage_need(regular: RegularFixture) -> None:
    winner = regular.mains[-1]
    requirement = regular.requirement(winner, GearSlotCode.HEAD)
    winner.gear_slots.append(
        CharacterGearSlot(
            gear_slot=requirement.gear_slot,
            current_classification=GearClassification.GARBAGE,
        )
    )
    regular.session.add(
        CharacterFloorBookBalance(
            character=winner,
            raid_floor=regular.floors[2],
            earned=999,
        )
    )
    regular.session.commit()
    assert assignment(regular.result(), "Head Coffer").recipient.character_name == winner.name


def test_no_savage_need_is_free_roll(regular: RegularFixture) -> None:
    regular.complete_all_savage()
    row = assignment(regular.result(), "Head Coffer")
    assert row.disposition is PlannedLootDisposition.FREE_ROLL and row.recipient is None


def test_material_fewest_confirmed_grants_wins_independently(regular: RegularFixture) -> None:
    for character in regular.mains:
        regular.set_augmented_need(character, GearSlotCode.EARRINGS, "ACCESSORY_GLAZE")
        regular.set_augmented_need(character, GearSlotCode.BODY, "ARMOR_TWINE")
    regular.grant(regular.mains[-1], "ACCESSORY_GLAZE", 2)
    regular.grant(regular.mains[-2], "ARMOR_TWINE", 2)
    result = regular.result()
    assert assignment(result, "Glaze").recipient.character_name == "Main 7"
    assert assignment(result, "Twine").recipient.character_name == "Main 8"


def test_material_remaining_need_then_job_then_roster_break_ties(regular: RegularFixture) -> None:
    first = regular.mains[0]
    second = regular.mains[1]
    sam = regular.mains[-1].job
    first.job = second.job = sam
    regular.set_augmented_need(first, GearSlotCode.EARRINGS, "ACCESSORY_GLAZE")
    regular.set_augmented_need(first, GearSlotCode.NECKLACE, "ACCESSORY_GLAZE")
    regular.set_augmented_need(second, GearSlotCode.EARRINGS, "ACCESSORY_GLAZE")
    assert assignment(regular.result(), "Glaze").recipient.character_name == first.name

    regular.set_augmented_need(second, GearSlotCode.NECKLACE, "ACCESSORY_GLAZE")
    assert assignment(regular.result(), "Glaze").recipient.character_name == first.name

    first.job = regular.session.scalar(select(Job).where(Job.abbreviation == "WAR"))
    regular.session.commit()
    assert assignment(regular.result(), "Glaze").recipient.character_name == second.name


def test_manual_material_ownership_affects_need_not_fairness_and_base_is_not_required(
    regular: RegularFixture,
) -> None:
    high = regular.mains[-1]
    low = regular.mains[-2]
    regular.set_augmented_need(high, GearSlotCode.EARRINGS, "ACCESSORY_GLAZE")
    regular.set_augmented_need(low, GearSlotCode.EARRINGS, "ACCESSORY_GLAZE")
    regular.session.add(
        CharacterAugmentationInventory(
            character=high,
            augmentation_material_type=regular.materials["ACCESSORY_GLAZE"],
            quantity=1,
        )
    )
    regular.session.commit()
    row = assignment(regular.result(), "Glaze")
    assert row.recipient.character_name == low.name
    assert row.fairness.confirmed_reclear_grants == 0
    assert row.fairness.current_remaining_need == 1


def test_no_material_need_is_free_roll_and_creates_no_history(regular: RegularFixture) -> None:
    before = regular.session.scalar(select(func.count()).select_from(ConfirmedReclearMaterialGrant))
    result = regular.result()
    assert assignment(result, "Twine").disposition is PlannedLootDisposition.FREE_ROLL
    assert assignment(result, "Glaze").disposition is PlannedLootDisposition.FREE_ROLL
    after = regular.session.scalar(select(func.count()).select_from(ConfirmedReclearMaterialGrant))
    assert after == before == 0


def test_result_is_deterministic_serializable_and_read_only(
    regular: RegularFixture, engine
) -> None:
    regular.session.add(
        CharacterFloorBookBalance(
            character=regular.mains[0],
            raid_floor=regular.floors[1],
            earned=10,
            manual_adjustment=4,
        )
    )
    regular.session.add(
        CharacterAugmentationInventory(
            character=regular.mains[0],
            augmentation_material_type=regular.materials["ARMOR_TWINE"],
            quantity=3,
        )
    )
    regular.session.add(AuditLog(static=regular.static, action="BEFORE", entity_type="Test"))
    regular.session.commit()
    before = {
        "gear": regular.session.scalar(select(func.count()).select_from(CharacterGearSlot)),
        "books": regular.session.scalar(
            select(func.count()).select_from(CharacterFloorBookBalance)
        ),
        "materials": regular.session.scalar(
            select(func.count()).select_from(CharacterAugmentationInventory)
        ),
        "weeks": regular.session.scalar(select(func.count()).select_from(ReclearWeek)),
        "audits": regular.session.scalar(select(func.count()).select_from(AuditLog)),
        "grants": regular.session.scalar(
            select(func.count()).select_from(ConfirmedReclearMaterialGrant)
        ),
    }
    writes: list[str] = []

    def record_writes(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", record_writes)
    try:
        first = regular.result()
        second = regular.result()
    finally:
        event.remove(engine, "before_cursor_execute", record_writes)
    assert first == second
    assert asdict(first)["run"]["name"] == "Regular"
    assert writes == [] and not regular.session.new and not regular.session.dirty
    after = {
        "gear": regular.session.scalar(select(func.count()).select_from(CharacterGearSlot)),
        "books": regular.session.scalar(
            select(func.count()).select_from(CharacterFloorBookBalance)
        ),
        "materials": regular.session.scalar(
            select(func.count()).select_from(CharacterAugmentationInventory)
        ),
        "weeks": regular.session.scalar(select(func.count()).select_from(ReclearWeek)),
        "audits": regular.session.scalar(select(func.count()).select_from(AuditLog)),
        "grants": regular.session.scalar(
            select(func.count()).select_from(ConfirmedReclearMaterialGrant)
        ),
    }
    assert after == before
