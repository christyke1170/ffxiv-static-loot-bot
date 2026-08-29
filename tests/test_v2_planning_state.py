"""Neutral V2 planning-state and pure-planner regression coverage."""

from datetime import date

import pytest
from sqlalchemy import event, select

from app.models import (
    Character,
    CharacterKind,
    ClearMode,
    DiscordGuild,
    Job,
    Static,
    StaticMember,
    V2Plan,
    V2ResourceBalance,
    WeeklyLockout,
)
from app.services import generate_regular_plan_v2, generate_split_plan_v2
from app.services.hierarchy import ensure_default_hierarchy
from app.services.planning_state import load_planning_state
from app.services.reclear import create_reclear_week
from app.services.seed import seed_reference_data
from app.services.v2_plan_orchestration import generate_and_persist_weekly_plan
from app.services.v2_plan_persistence import load_persisted_plan_v2
from app.services.v2_plan_state_fingerprint import planning_state_fingerprint


def _static(session):
    seed_reference_data(session)
    static = Static(guild=DiscordGuild(discord_guild_id=991, name="Neutral"), name="V2")
    jobs = {
        code: session.scalar(select(Job).where(Job.abbreviation == code))
        for code in ("PLD", "WAR", "WHM", "SCH", "MNK", "DRG", "NIN", "BRD")
    }
    job_codes = ("PLD", "WAR", "WHM", "SCH", "MNK", "DRG", "NIN", "BRD")
    for index in range(8):
        member = StaticMember(static=static, discord_user_id=1000 + index, display_name=f"P{index}")
        session.add(
            Character(
                static_member=member,
                job=jobs[job_codes[index]],
                name=f"Main{index}",
                world="Neutral",
                kind=CharacterKind.MAIN,
            )
        )
        session.add(
            Character(
                static_member=member,
                job=jobs[job_codes[index]],
                name=f"Alt{index}",
                world="Neutral",
                kind=CharacterKind.ALT,
            )
        )
    session.flush()
    ensure_default_hierarchy(session, static)
    return static


def test_regular_state_loads_roster_hierarchy_floors_and_ownership(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    state = load_planning_state(session, static.id, week.id)
    assert len(state.mains) == 8
    assert len(state.alts) == 8
    assert len(state.ownership) == 8
    assert [floor.floor_number for floor in state.floors] == [1, 2, 3, 4]
    assert state.hierarchy
    assert all(character.needs is not None for character in (*state.mains, *state.alts))


def test_regular_planner_is_pure_and_retry_is_deterministic(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    state = load_planning_state(session, static.id, week.id)
    first = generate_regular_plan_v2(state)
    second = generate_regular_plan_v2(state)
    assert first == second
    assert first.mode is ClearMode.REGULAR
    assert first.fingerprint
    assert not session.new and not session.dirty


def test_split_roster_loads_all_active_mains_and_alts(session):
    static = _static(session)
    week = create_reclear_week(
        session,
        static,
        ClearMode.SPLIT,
        week_start=date(2026, 8, 24),
        split_a_main_member_ids={m.id for m in sorted(static.members, key=lambda m: m.id)[:4]},
    )
    state = load_planning_state(session, static.id, week.id)
    assert len(state.mains) == len(state.alts) == 8


def test_missing_alt_is_rejected(session):
    static = _static(session)
    alt = next(
        character
        for member in static.members
        for character in member.characters
        if character.kind is CharacterKind.ALT
    )
    alt.active = False
    with pytest.raises(Exception, match="[Aa]lt|eight"):
        create_reclear_week(
            session,
            static,
            ClearMode.SPLIT,
            week_start=date(2026, 8, 24),
            split_a_main_member_ids={m.id for m in sorted(static.members, key=lambda m: m.id)[:4]},
        )


def test_duplicate_ownership_is_rejected():
    from tests.v2_test_helpers import split_state

    value = split_state()
    duplicate = value.__class__(
        value.static_id,
        value.static_name,
        value.week_id,
        value.week_number,
        value.week_start,
        value.week_status,
        value.mode,
        value.reset_period,
        value.mains,
        value.alts,
        value.ownership + ((1, 10),),
        value.groups,
        value.floors,
        value.lockouts,
        value.hierarchy,
        value.active_plan,
        value.fairness,
        value.warnings,
    )
    with pytest.raises(Exception, match="exactly eight|duplicate"):
        generate_split_plan_v2(duplicate)


def test_main_alt_job_mismatch_is_rejected():
    from tests.v2_test_helpers import character, split_state

    value = split_state()
    alts = (character(9, 1, CharacterKind.ALT, "TANK", position=1), *value.alts[1:])
    malformed = value.__class__(
        value.static_id,
        value.static_name,
        value.week_id,
        value.week_number,
        value.week_start,
        value.week_status,
        value.mode,
        value.reset_period,
        value.mains,
        alts,
        value.ownership,
        value.groups,
        value.floors,
        value.lockouts,
        value.hierarchy,
        value.active_plan,
        value.fairness,
        value.warnings,
    )
    with pytest.raises(Exception, match="job"):
        generate_split_plan_v2(malformed)


def test_invalid_role_composition_is_rejected():
    from tests.v2_test_helpers import character, split_state

    value = split_state()
    mains = (character(1, 1, CharacterKind.MAIN, "UNKNOWN", position=1), *value.mains[1:])
    malformed = value.__class__(
        value.static_id,
        value.static_name,
        value.week_id,
        value.week_number,
        value.week_start,
        value.week_status,
        value.mode,
        value.reset_period,
        mains,
        value.alts,
        value.ownership,
        value.groups,
        value.floors,
        value.lockouts,
        value.hierarchy,
        value.active_plan,
        value.fairness,
        value.warnings,
    )
    with pytest.raises(Exception, match="role"):
        generate_split_plan_v2(malformed)


def test_missing_static_job_bis_produces_warning(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    state = load_planning_state(session, static.id, week.id)
    assert any(
        "no Static + Job BiS" in warning
        for character in state.mains
        for warning in character.needs.configuration_warnings
    )


def test_neutral_resource_balances_are_loaded(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    session.add(
        V2ResourceBalance(
            static_id=static.id,
            recipient_id=week.participants[0].character_id,
            resource_key="BOOK_FLOOR_1",
            quantity=3,
        )
    )
    session.flush()
    state = load_planning_state(session, static.id, week.id)
    assert state.mains[0].needs.book_balances[0].available == 3


def test_lockouts_are_loaded_by_neutral_floor_number(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    session.commit()
    character = week.participants[0].character
    session.add(
        WeeklyLockout(
            character_id=character.id,
            floor_number=2,
            week_start=week.week_start,
            cleared=True,
            loot_eligible=False,
        )
    )
    session.flush()
    state = load_planning_state(session, static.id, week.id)
    assert state.lockouts[0].floor_number == 2
    assert state.lockouts[0].loot_eligible is False


def test_active_plan_metadata_is_loaded(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    generate_and_persist_weekly_plan(session, static.id, week.id, actor_id=7)
    state = load_planning_state(session, static.id, week.id)
    assert state.active_plan is not None
    assert state.active_plan.mode is ClearMode.REGULAR


def test_loading_planning_state_performs_no_writes(session, engine):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    session.commit()
    writes = []

    def capture(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        load_planning_state(session, static.id, week.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert writes == []


def test_active_plan_metadata_does_not_change_state_fingerprint(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    before = load_planning_state(session, static.id, week.id)
    generate_and_persist_weekly_plan(session, static.id, week.id, actor_id=7)
    after = load_planning_state(session, static.id, week.id)
    assert planning_state_fingerprint(before) == planning_state_fingerprint(after)


def test_split_state_has_opposite_main_alt_pairs_and_exactly_35_partitions(session):
    static = _static(session)
    members = sorted(static.members, key=lambda row: row.id)
    week = create_reclear_week(
        session,
        static,
        ClearMode.SPLIT,
        week_start=date(2026, 8, 24),
        split_a_main_member_ids={member.id for member in members[:4]},
    )
    state = load_planning_state(session, static.id, week.id)
    proposal = generate_split_plan_v2(state)
    assert len(state.ownership) == 8
    assert proposal.partitions_evaluated == 35
    assert len(proposal.groups) == 2
    assert set(proposal.groups[0].participant_ids).isdisjoint(proposal.groups[1].participant_ids)


def test_regular_v2_plan_round_trips_immutably_with_actor_metadata(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    result = generate_and_persist_weekly_plan(session, static.id, week.id, actor_id=777)
    stored = session.get(V2Plan, result.plan_id)
    assert stored is not None
    assert stored.actor_id == 777
    assert result == load_persisted_plan_v2(session, result.plan_id)
    assert result.proposal.mode is ClearMode.REGULAR


def test_exact_v2_retry_returns_existing_plan_without_regeneration(session):
    static = _static(session)
    week = create_reclear_week(session, static, ClearMode.REGULAR, week_start=date(2026, 8, 24))
    first = generate_and_persist_weekly_plan(session, static.id, week.id, actor_id=777)
    second = generate_and_persist_weekly_plan(session, static.id, week.id, actor_id=888)
    assert second == first
    assert session.query(V2Plan).filter_by(reclear_week_id=week.id).count() == 1
