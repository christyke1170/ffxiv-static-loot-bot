"""Pure Split Savage-coffer assignment and selection tests."""

from dataclasses import asdict

from sqlalchemy import event, func, select

from app.models import (
    BisSet,
    BisSetItem,
    CharacterBisSelection,
    CharacterGearSlot,
    CharacterKind,
    GearClassification,
    GearSlotCode,
    LootAssignment,
    PlannedLootDisposition,
)
from app.schemas.loot_planning import SplitSavagePlanResult
from app.services import plan_split_savage_loot
from tests.test_regular_loot_planning import RegularFixture


def make_split_savage_fixture(session) -> RegularFixture:
    fixture = RegularFixture(session)
    role_jobs = ("PLD", "WAR", "WHM", "SCH", "SAM", "DRG", "BRD", "BLM")
    jobs = {row.abbreviation: row for row in session.query(type(fixture.mains[0].job)).all()}
    for main, alt, job_code in zip(fixture.mains, fixture.alts, role_jobs, strict=True):
        main.job = jobs[job_code]
        alt.job = main.job
    session.commit()
    return fixture


def assignments(result):
    return result.winner.run_a.assignments + result.winner.run_b.assignments


def test_split_savage_assigns_twenty_coffers_in_physical_runs_and_warns_for_missing_alt_bis(
    session,
):
    fixture = make_split_savage_fixture(session)
    result = plan_split_savage_loot(session, fixture.static.id)

    assert isinstance(result, SplitSavagePlanResult)
    assert result.is_valid
    assert result.valid_candidates_evaluated == 35
    assert len(result.winner.run_a.assignments) == 10
    assert len(result.winner.run_b.assignments) == 10
    assert all(
        row.loot_label
        in {
            "Earring Coffer",
            "Necklace Coffer",
            "Bracelet Coffer",
            "Ring Coffer",
            "Head Coffer",
            "Gloves Coffer",
            "Boots Coffer",
            "Chest Coffer",
            "Pants Coffer",
            "Weapon Coffer",
        }
        for row in assignments(result)
    )
    assert all(row.recipient_designation is CharacterKind.MAIN for row in assignments(result))
    assert all(row.recipient is not None for row in assignments(result))
    assert any("no selected BiS" in issue.message for issue in result.warnings)


def test_main_first_beats_alt_and_allows_unlimited_funneling(session):
    fixture = make_split_savage_fixture(session)
    result = plan_split_savage_loot(session, fixture.static.id)
    recipients = [row.recipient.character_name for row in assignments(result)]
    assert recipients
    assert len(recipients) > len(set(recipients))
    assert all(row.recipient_designation is CharacterKind.MAIN for row in assignments(result))


def test_missing_main_need_is_free_roll_and_completed_slot_is_not_eligible(session):
    fixture = make_split_savage_fixture(session)
    for main in fixture.mains:
        requirement = fixture.requirement(main, GearSlotCode.HEAD)
        main.gear_slots.append(
            CharacterGearSlot(
                gear_slot=requirement.gear_slot,
                current_classification=GearClassification.SAVAGE,
            )
        )
    session.commit()
    result = plan_split_savage_loot(session, fixture.static.id)
    head_rows = [row for row in assignments(result) if row.loot_label == "Head Coffer"]
    assert head_rows and all(
        row.disposition is PlannedLootDisposition.FREE_ROLL for row in head_rows
    )


def test_repeated_calls_are_deterministic_and_do_not_write_or_mutate_orm(session, engine):
    fixture = make_split_savage_fixture(session)
    before = [
        (character.id, character.job_id, character.kind, character.active)
        for character in (*fixture.mains, *fixture.alts)
    ]
    writes = []

    def record_writes(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", record_writes)
    try:
        first = plan_split_savage_loot(session, fixture.static.id)
        second = plan_split_savage_loot(session, fixture.static.id)
    finally:
        event.remove(engine, "before_cursor_execute", record_writes)
    after = [
        (character.id, character.job_id, character.kind, character.active)
        for character in (*fixture.mains, *fixture.alts)
    ]
    assert first == second
    assert isinstance(asdict(first), dict)
    assert writes == [] and before == after
    assert session.scalar(select(func.count()).select_from(LootAssignment)) == 0


def test_no_valid_roster_propagates_step_three_validation(session):
    fixture = make_split_savage_fixture(session)
    fixture.static.members[0].active = False
    session.commit()
    result = plan_split_savage_loot(session, fixture.static.id)
    assert not result.is_valid
    assert result.winner is None
    assert result.valid_candidates_evaluated == 0


def test_split_savage_result_exposes_scores_and_canonical_winner(session):
    fixture = make_split_savage_fixture(session)
    result = plan_split_savage_loot(session, fixture.static.id)

    assert result.winner is not None and result.runner_up is not None
    assert len(result.winner.main_assignment_vector) == 21
    assert len(result.winner.alt_assignment_vector) == 21
    assert result.winner.candidate_ordinal <= 35
    assert result.selection_reasoning
    assert result.winner.comparison_key[-1] == -result.winner.candidate_ordinal
    assert all(
        row.candidate_ordinal == result.winner.candidate_ordinal for row in assignments(result)
    )


def test_alt_receives_only_when_no_main_needs_the_coffer(session):
    fixture = make_split_savage_fixture(session)
    main = fixture.mains[0]
    requirement = fixture.requirement(main, GearSlotCode.HEAD)
    requirement.classification = GearClassification.NOT_APPLICABLE
    requirement.desired_item = None
    requirement.desired_item_id = None
    requirement.raid_floor = None
    requirement.raid_floor_id = None
    requirement.loot_type = None
    requirement.loot_type_id = None
    requirement.book_cost = None
    for other_main in fixture.mains[1:]:
        other_requirement = fixture.requirement(other_main, GearSlotCode.HEAD)
        other_main.gear_slots.append(
            CharacterGearSlot(
                gear_slot=other_requirement.gear_slot,
                current_classification=GearClassification.SAVAGE,
            )
        )
    alt = fixture.alts[0]
    alt.job = session.scalar(select(type(alt.job)).where(type(alt.job).abbreviation == "WAR"))
    main_bis = fixture.selections[fixture.mains[1].id].bis_set
    alt_bis = BisSet(job=alt.job, raid_tier=fixture.tier, name="Alt fallback BiS")
    for source in main_bis.items:
        alt_bis.items.append(
            BisSetItem(
                gear_slot=source.gear_slot,
                classification=(
                    GearClassification.SAVAGE
                    if source.gear_slot.code is GearSlotCode.HEAD
                    else GearClassification.NOT_APPLICABLE
                ),
                desired_item=(
                    source.desired_item if source.gear_slot.code is GearSlotCode.HEAD else None
                ),
                raid_floor=(
                    source.raid_floor if source.gear_slot.code is GearSlotCode.HEAD else None
                ),
                loot_type=(
                    source.loot_type if source.gear_slot.code is GearSlotCode.HEAD else None
                ),
                book_cost=source.book_cost if source.gear_slot.code is GearSlotCode.HEAD else None,
            )
        )
    session.add(CharacterBisSelection(character=alt, raid_tier=fixture.tier, bis_set=alt_bis))
    session.commit()
    result = plan_split_savage_loot(session, fixture.static.id)
    head_rows = [row for row in assignments(result) if row.loot_label == "Head Coffer"]
    assert any(
        row.recipient is not None
        and row.recipient.character_name == alt.name
        and row.recipient_designation is CharacterKind.ALT
        for row in head_rows
    )
