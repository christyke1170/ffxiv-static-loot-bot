"""Step 6 generated-plan persistence tests using fictional planning data."""

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    CharacterAugmentationInventory,
    ClearMode,
    ConfirmedReclearMaterialGrant,
    LootAssignment,
    LootPlan,
    LootPlanParticipant,
    LootPlanRun,
    ReclearFloorCompletion,
    ReclearWeek,
    WeeklyLockout,
    WeeklyLootPlanStatus,
)
from app.schemas.loot_plan_persistence import (
    ActiveLootPlanError,
    LootPlanValidationError,
    PersistedLootPlanNotFound,
)
from app.services import generate_and_persist_loot_plan, load_persisted_loot_plan
from tests.test_regular_loot_planning import RegularFixture
from tests.test_split_savage_planning import make_split_savage_fixture


def _counts(session):
    return {
        "plans": session.scalar(select(func.count()).select_from(LootPlan)),
        "runs": session.scalar(select(func.count()).select_from(LootPlanRun)),
        "participants": session.scalar(select(func.count()).select_from(LootPlanParticipant)),
        "assignments": session.scalar(select(func.count()).select_from(LootAssignment)),
        "grants": session.scalar(select(func.count()).select_from(ConfirmedReclearMaterialGrant)),
        "weeks": session.scalar(select(func.count()).select_from(ReclearWeek)),
    }


def test_regular_generated_plan_round_trips_without_side_effects(session):
    fixture = RegularFixture(session)
    before = _counts(session)
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 7001)
    session.commit()

    assert result.status is WeeklyLootPlanStatus.READY
    assert result.mode is ClearMode.REGULAR
    assert result.target_week == 2
    assert len(result.runs) == 1
    assert len(result.runs[0].participants) == 8
    assert len(result.runs[0].assignments) == 12
    assert all(
        row.recipient_designation is None or row.recipient_designation.value == "MAIN"
        for row in result.runs[0].assignments
    )
    assert (
        session.scalar(select(func.count()).select_from(ConfirmedReclearMaterialGrant))
        == before["grants"]
    )
    assert session.scalar(select(func.count()).select_from(CharacterAugmentationInventory)) == 0
    assert session.scalar(select(func.count()).select_from(ReclearFloorCompletion)) == 0
    assert session.scalar(select(func.count()).select_from(WeeklyLockout)) == 0
    assert session.scalar(select(func.count()).select_from(AuditLog)) == 1

    loaded = load_persisted_loot_plan(session, result.plan_id)
    assert loaded == result
    assert [row.loot_label for row in loaded.runs[0].assignments] == [
        row.loot_label for row in result.runs[0].assignments
    ]


def test_split_generated_plan_persists_materials_and_paired_weapon_components(session):
    fixture = make_split_savage_fixture(session)
    from app.models import FloorLootRule, LootCategory, LootType

    for number, code in ((2, "WEAPON_TOMESTONE"), (3, "WEAPON_AUGMENT")):
        loot_type = LootType(
            raid_tier=fixture.tier,
            code=code,
            name=code.replace("_", " ").title(),
            category=LootCategory.OTHER,
        )
        fixture.floors[number].loot_rules.append(
            FloorLootRule(loot_type=loot_type, expected_quantity=1)
        )
    session.commit()
    result = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.SPLIT, 7002)
    session.commit()

    assert result.status is WeeklyLootPlanStatus.READY
    assert len(result.runs) == 2
    assert all(len(run.participants) == 8 for run in result.runs)
    labels = {row.loot_label for run in result.runs for row in run.assignments}
    assert {"Twine", "Glaze", "Weapon Tomestone", "Weapon Augment"} <= labels
    for run in result.runs:
        weapon_rows = [
            row
            for row in run.assignments
            if row.loot_label in {"Weapon Tomestone", "Weapon Augment"}
        ]
        assert len(weapon_rows) == 2
        assert weapon_rows[0].recipient_id == weapon_rows[1].recipient_id
        assert weapon_rows[0].paired_assignment_id == weapon_rows[1].assignment_id
    assert session.scalar(select(func.count()).select_from(ConfirmedReclearMaterialGrant)) == 0

    loaded = load_persisted_loot_plan(session, result.plan_id)
    assert loaded == result


def test_active_ready_plan_blocks_regeneration_without_overwrite(session):
    fixture = RegularFixture(session)
    first = generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 7003)
    session.commit()
    with pytest.raises(ActiveLootPlanError):
        generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 7004)
    assert session.get(LootPlan, first.plan_id).status is WeeklyLootPlanStatus.READY


def test_loading_unknown_plan_is_typed_and_read_only(session):
    with pytest.raises(PersistedLootPlanNotFound):
        load_persisted_loot_plan(session, 999999)


def test_persistence_rejects_invalid_mode_and_leaves_no_graph(session):
    fixture = RegularFixture(session)
    with pytest.raises(LootPlanValidationError):
        generate_and_persist_loot_plan(session, fixture.static.id, "REGULAR", 7005)
    assert _counts(session)["plans"] == 0


def test_partial_persistence_failure_rolls_back_the_new_graph(monkeypatch, session):
    fixture = RegularFixture(session)
    import app.services.loot_plan_persistence as persistence

    def fail(*_args, **_kwargs):
        raise ValueError("simulated assignment failure")

    monkeypatch.setattr(persistence, "_resolve_configuration", fail)
    with pytest.raises(LootPlanValidationError):
        generate_and_persist_loot_plan(session, fixture.static.id, ClearMode.REGULAR, 7006)
    assert _counts(session)["plans"] == 0
    assert _counts(session)["runs"] == 0
    assert _counts(session)["participants"] == 0
    assert _counts(session)["assignments"] == 0
    assert _counts(session)["weeks"] == 0
