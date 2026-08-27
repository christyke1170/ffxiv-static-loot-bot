"""Pure Split-roster candidate generation tests with fictional characters."""

from dataclasses import asdict

import pytest
from sqlalchemy import event, select

import app.services.loot_planning as split_service
from app.models import Character, CharacterKind, DiscordGuild, Job, RaidTier, Static, StaticMember
from app.schemas.loot_planning import (
    CombatRole,
    LootPlanningIssueCode,
    SplitRejectionCode,
    SplitRoleCounts,
    SplitRosterRun,
)
from app.services import generate_split_roster_candidates, seed_reference_data
from tests.test_regular_loot_planning import RegularFixture


class SplitFixture:
    def __init__(self, session) -> None:
        self.session = session
        seed_reference_data(session)
        self.tier = RaidTier(code="SPLIT_CANDIDATES", name="Fictional Split Tier")
        self.static = Static(
            guild=DiscordGuild(discord_guild_id=772001, name="Split Guild"),
            name="Split Static",
            active_raid_tier=self.tier,
        )
        jobs = {
            row.abbreviation: row
            for row in session.scalars(
                select(Job).where(
                    Job.abbreviation.in_(
                        (
                            "PLD",
                            "WAR",
                            "WHM",
                            "SCH",
                            "SAM",
                            "DRG",
                            "BRD",
                            "MCH",
                            "BLM",
                            "SMN",
                        )
                    )
                )
            )
        }
        main_jobs = ("PLD", "WAR", "WHM", "SCH", "SAM", "DRG", "BRD", "BLM")
        alt_jobs = ("WAR", "PLD", "SCH", "WHM", "DRG", "SAM", "MCH", "SMN")
        self.members: list[StaticMember] = []
        self.mains: list[Character] = []
        self.alts: list[Character] = []
        for index, (main_job, alt_job) in enumerate(zip(main_jobs, alt_jobs, strict=True), 1):
            member = StaticMember(
                static=self.static,
                discord_user_id=772100 + index,
                display_name=f"Split Member {index}",
            )
            main = Character(
                static_member=member,
                job=jobs[main_job],
                name=f"Split Main {index}",
                world="Fictional",
                kind=CharacterKind.MAIN,
            )
            alt = Character(
                static_member=member,
                job=jobs[alt_job],
                name=f"Split Alt {index}",
                world="Fictional",
                kind=CharacterKind.ALT,
            )
            self.members.append(member)
            self.mains.append(main)
            self.alts.append(alt)
            session.add_all([main, alt])
        session.commit()

    def result(self):
        return generate_split_roster_candidates(self.session, self.static.id)

    def job(self, abbreviation: str) -> Job:
        return self.session.scalar(select(Job).where(Job.abbreviation == abbreviation))


@pytest.fixture
def split(session) -> SplitFixture:
    return SplitFixture(session)


def issue_codes(result) -> set[LootPlanningIssueCode]:
    return {issue.code for issue in result.issues}


def test_exactly_eight_active_members_generate_all_35_partitions(split: SplitFixture) -> None:
    result = split.result()
    assert result.is_valid
    assert result.total_partitions_evaluated == 35
    assert result.total_candidates_rejected == 0
    assert result.total_valid_candidates == 35
    assert result.target_week == 2
    assert [row.partition_ordinal for row in result.candidates] == list(range(1, 36))


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (CharacterKind.MAIN, LootPlanningIssueCode.MISSING_MAIN),
        (CharacterKind.ALT, LootPlanningIssueCode.MISSING_ALT),
    ],
)
def test_missing_binding_is_rejected(split: SplitFixture, kind, expected) -> None:
    character = split.mains[0] if kind is CharacterKind.MAIN else split.alts[0]
    split.session.delete(character)
    split.session.commit()
    result = split.result()
    assert not result.is_valid and result.candidates == () and expected in issue_codes(result)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (CharacterKind.MAIN, LootPlanningIssueCode.DUPLICATE_MAIN),
        (CharacterKind.ALT, LootPlanningIssueCode.DUPLICATE_ALT),
    ],
)
def test_duplicate_binding_is_rejected(split: SplitFixture, kind, expected) -> None:
    original = split.mains[0] if kind is CharacterKind.MAIN else split.alts[0]
    split.session.add(
        Character(
            static_member=split.members[0],
            job=original.job,
            name=f"Duplicate {kind.value}",
            world="Fictional",
            kind=kind,
        )
    )
    split.session.commit()
    result = split.result()
    assert not result.is_valid and result.candidates == () and expected in issue_codes(result)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (CharacterKind.MAIN, LootPlanningIssueCode.INACTIVE_MAIN),
        (CharacterKind.ALT, LootPlanningIssueCode.INACTIVE_ALT),
    ],
)
def test_inactive_binding_is_rejected(split: SplitFixture, kind, expected) -> None:
    character = split.mains[0] if kind is CharacterKind.MAIN else split.alts[0]
    character.active = False
    split.session.commit()
    result = split.result()
    assert not result.is_valid and result.candidates == () and expected in issue_codes(result)


def test_inactive_member_causes_invalid_member_count(split: SplitFixture) -> None:
    split.members[0].active = False
    split.session.commit()
    result = split.result()
    assert not result.is_valid
    assert LootPlanningIssueCode.INVALID_MEMBER_COUNT in issue_codes(result)


def test_cross_member_binding_is_rejected_defensively(split: SplitFixture, monkeypatch) -> None:
    loaded = split_service._load_split_static(split.session, split.static.id)
    main = loaded.members[0].characters[0]
    original_id = main.static_member_id
    main.static_member_id = loaded.members[1].id
    monkeypatch.setattr(split_service, "_load_split_static", lambda *_: loaded)
    result = split.result()
    main.static_member_id = original_id
    split.session.rollback()
    assert not result.is_valid
    assert LootPlanningIssueCode.INVALID_MAIN_BINDING in issue_codes(result)


def test_cross_static_character_is_rejected_defensively(split: SplitFixture, monkeypatch) -> None:
    other = Static(
        guild=split.static.guild,
        name="Other Split Static",
        active_raid_tier=split.tier,
    )
    foreign_member = StaticMember(
        static=other,
        discord_user_id=773001,
        display_name="Foreign Member",
    )
    split.session.add(foreign_member)
    split.session.commit()
    loaded = split_service._load_split_static(split.session, split.static.id)
    main = loaded.members[0].characters[0]
    original_member = main.static_member
    main.__dict__["static_member"] = foreign_member
    monkeypatch.setattr(split_service, "_load_split_static", lambda *_: loaded)
    result = split.result()
    main.__dict__["static_member"] = original_member
    split.session.rollback()
    assert not result.is_valid
    assert LootPlanningIssueCode.CROSS_STATIC_CHARACTER in issue_codes(result)


def test_unsupported_job_and_unknown_role_are_fatal(split: SplitFixture) -> None:
    split.mains[0].job = Job(
        abbreviation="BAD",
        name="Unsupported Split Job",
        role="Tank",
    )
    split.alts[0].job.role = "Crafter"
    split.session.commit()
    result = split.result()
    assert not result.is_valid and result.candidates == ()
    assert {
        LootPlanningIssueCode.UNSUPPORTED_JOB,
        LootPlanningIssueCode.UNKNOWN_COMBAT_ROLE,
    } <= issue_codes(result)


def test_mirrors_are_unique_and_first_member_is_always_run_a_main(split: SplitFixture) -> None:
    result = split.result()
    signatures = []
    for candidate in result.candidates:
        a_mains = tuple(
            row.roster_order
            for row in candidate.run_a.participants
            if row.designation is CharacterKind.MAIN
        )
        b_mains = tuple(
            row.roster_order
            for row in candidate.run_b.participants
            if row.designation is CharacterKind.MAIN
        )
        assert 1 in a_mains and 1 not in b_mains
        signatures.append((a_mains, b_mains))
    assert len(signatures) == len(set(signatures)) == 35
    assert not any((b, a) in signatures for a, b in signatures)


def test_candidate_order_is_deterministic(split: SplitFixture) -> None:
    first = split.result()
    second = split.result()
    assert first == second
    assert [row.candidate_identifier for row in first.candidates] == [
        row.candidate_identifier for row in second.candidates
    ]


def test_every_valid_candidate_has_exact_complementary_runs(split: SplitFixture) -> None:
    for candidate in split.result().candidates:
        for run in (candidate.run_a, candidate.run_b):
            assert len(run.participants) == 8
            assert len({row.static_member_id for row in run.participants}) == 8
            assert len({row.character_id for row in run.participants}) == 8
            assert sum(row.designation is CharacterKind.MAIN for row in run.participants) == 4
            assert sum(row.designation is CharacterKind.ALT for row in run.participants) == 4
            assert run.role_counts == SplitRoleCounts(2, 2, 4)
            assert [row.roster_order for row in run.participants] == list(range(1, 9))
        by_a = {row.static_member_id: row for row in candidate.run_a.participants}
        by_b = {row.static_member_id: row for row in candidate.run_b.participants}
        assert set(by_a) == set(by_b)
        assert all(by_a[key].designation is not by_b[key].designation for key in by_a)
        assert all(by_a[key].character_id != by_b[key].character_id for key in by_a)


def test_dps_subroles_all_count_as_dps(split: SplitFixture) -> None:
    result = split.result()
    dps_jobs = {"SAM", "DRG", "BRD", "MCH", "BLM", "SMN"}
    assert dps_jobs <= {
        row.job
        for candidate in result.candidates
        for run in (candidate.run_a, candidate.run_b)
        for row in run.participants
        if row.combat_role is CombatRole.DPS
    }
    assert all(candidate.run_a.role_counts.dps == 4 for candidate in result.candidates)


def test_one_tank_run_rejects_candidate_and_no_rule_is_relaxed(split: SplitFixture) -> None:
    split.alts[0].job = split.job("SAM")
    split.session.commit()
    result = split.result()
    assert not result.is_valid and result.total_valid_candidates == 0
    assert result.total_partitions_evaluated == result.total_candidates_rejected == 35
    assert LootPlanningIssueCode.NO_VALID_SPLIT_COMPOSITION in issue_codes(result)
    one_tank = [
        row
        for row in result.rejections
        if row.code is SplitRejectionCode.INVALID_ROLE_COMPOSITION and row.role_counts.tanks == 1
    ]
    assert len(one_tank) == 35
    assert all(row.run_name == "Split Run B" for row in one_tank)


def test_three_healer_run_is_rejected(split: SplitFixture) -> None:
    split.alts[4].job = split.job("WHM")
    split.session.commit()
    result = split.result()
    three_healers = [
        row
        for row in result.rejections
        if row.code is SplitRejectionCode.INVALID_ROLE_COMPOSITION and row.role_counts.healers == 3
    ]
    assert len(three_healers) == 35
    assert result.total_valid_candidates == 0


def test_invalid_total_is_rejected(monkeypatch, split: SplitFixture) -> None:
    original = split_service._build_split_run

    def malformed(*args, **kwargs):
        run = original(*args, **kwargs)
        if run.name == "Split Run A":
            return SplitRosterRun(run.name, run.participants[:-1], run.role_counts)
        return run

    monkeypatch.setattr(split_service, "_build_split_run", malformed)
    result = split.result()
    assert result.total_valid_candidates == 0
    assert all(
        any(
            rejection.code is SplitRejectionCode.INVALID_PARTICIPANT_COUNT
            for rejection in result.rejections
            if rejection.partition_ordinal == ordinal
        )
        for ordinal in range(1, 36)
    )


def test_composition_rejection_is_structured_not_fatal_binding_error(split: SplitFixture) -> None:
    split.alts[0].job = split.job("SAM")
    split.session.commit()
    result = split.result()
    assert result.total_partitions_evaluated == 35
    assert result.rejections
    assert issue_codes(result) == {LootPlanningIssueCode.NO_VALID_SPLIT_COMPOSITION}


def test_generation_is_read_only_serializable_and_does_not_mutate_orm(
    split: SplitFixture, engine
) -> None:
    before = [
        (row.id, row.static_member_id, row.job_id, row.kind, row.active)
        for row in (*split.mains, *split.alts)
    ]
    writes: list[str] = []

    def record_writes(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", record_writes)
    try:
        result = split.result()
    finally:
        event.remove(engine, "before_cursor_execute", record_writes)
    after = [
        (row.id, row.static_member_id, row.job_id, row.kind, row.active)
        for row in (*split.mains, *split.alts)
    ]
    assert writes == [] and not split.session.new and not split.session.dirty
    assert before == after
    serialized = asdict(result)
    assert serialized["candidates"][0]["run_a"]["participants"][0]["static_member_name"]


def test_regular_planning_regression_is_unchanged(session) -> None:
    regular = RegularFixture(session)
    before = regular.result()
    split = generate_split_roster_candidates(regular.session, regular.static.id)
    after = regular.result()
    assert not split.is_valid  # Regular fixture deliberately has unsupported fictional Alt jobs.
    assert before == after
