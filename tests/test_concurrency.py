"""Two-session duplicate confirmation and stale assignment safety tests."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.database import Base, create_database_engine, create_session_factory
from app.models import ClearMode, InventoryItem, LootReceipt
from app.services import (
    confirm_loot_received,
    generate_weekly_loot_plan,
    mark_reclear_floors_complete,
    override_assignment,
)
from tests.test_planning import PlanningFixture


def test_concurrent_duplicate_confirmation_creates_one_receipt(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'concurrent.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        fixture = PlanningFixture(session, ClearMode.REGULAR)
        fixture.select_bis(fixture.mains[0])
        assignment = generate_weekly_loot_plan(session, fixture.week.id).assignments[0].assignment
        mark_reclear_floors_complete(
            session, fixture.week.id, [(fixture.groups[0].id, fixture.floor.id)], 99
        )
        assignment_id = assignment.id
        session.commit()

    def confirm():
        try:
            with factory() as session:
                confirm_loot_received(session, assignment_id, True, 99)
                session.commit()
            return "ok"
        except Exception as error:  # conflicting stale transactions must fail, not duplicate
            return type(error).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _value: confirm(), range(2)))

    with factory() as session:
        assert results.count("ok") >= 1
        assert session.scalar(select(func.count()).select_from(LootReceipt)) == 1
        inventories = list(session.scalars(select(InventoryItem)))
        assert len(inventories) == 1 and inventories[0].quantity == 1
    engine.dispose()


def test_stale_override_after_receipt_is_rejected(session):
    fixture = PlanningFixture(session, ClearMode.REGULAR)
    fixture.select_bis(fixture.mains[0])
    assignment = generate_weekly_loot_plan(session, fixture.week.id).assignments[0].assignment
    mark_reclear_floors_complete(
        session, fixture.week.id, [(fixture.groups[0].id, fixture.floor.id)], 99
    )
    confirm_loot_received(session, assignment.id, True, 99)
    session.commit()

    with pytest.raises(ValueError, match="finalized"):
        override_assignment(
            session,
            fixture.static.id,
            assignment.id,
            fixture.mains[1].id,
            "stale override",
            99,
            force=True,
        )
