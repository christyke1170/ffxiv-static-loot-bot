"""Single side-by-side V2 boundary for generating and persisting weekly plans."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.models import AuditLog, ClearMode, ReclearWorkflowState, V2Plan
from app.services.planning_state import PlanningStateError, load_planning_state
from app.services.regular_planning_v2 import generate_regular_plan_v2
from app.services.split_planning_v2 import generate_split_plan_v2
from app.services.v2_plan_persistence import (
    V2PlanPersistenceError,
    load_persisted_plan_v2,
    persist_regular_plan_v2,
    persist_split_plan_v2,
)
from app.services.v2_plan_state_fingerprint import planning_state_fingerprint


class V2PlanOrchestrationError(ValueError):
    """The requested weekly V2 plan cannot be generated safely."""


def close_v2_week(session, week, actor_id: int):
    """Close an active V2 week without consulting legacy assignment state."""
    if actor_id is None:
        raise V2PlanOrchestrationError("An actor identity is required to close a V2 week.")
    if week.workflow_state is ReclearWorkflowState.CANCELLED:
        raise V2PlanOrchestrationError("A cancelled V2 week cannot be closed.")
    if week.workflow_state is ReclearWorkflowState.CLOSED:
        return week
    week.workflow_state = ReclearWorkflowState.CLOSED
    week.finalized_at = datetime.now(UTC)
    session.add(
        AuditLog(
            static_id=week.static_id,
            actor_discord_user_id=actor_id,
            action="V2_RECLEAR_WEEK_CLOSED",
            entity_type="ReclearWeek",
            entity_id=str(week.id),
            details="V2 confirmation workflow closed",
        )
    )
    session.flush()
    return week


def generate_and_persist_weekly_plan(session, static_id: int, week_id: int, actor_id: int):
    """Generate the current week's immutable V2 proposal and persist it atomically."""
    if actor_id is None:
        raise V2PlanOrchestrationError("An actor identity is required to generate a weekly plan.")
    try:
        state = load_planning_state(session, static_id, week_id)
        existing = session.scalar(
            select(V2Plan).where(
                V2Plan.reclear_week_id == week_id,
                V2Plan.static_id == static_id,
            )
        )
        if existing is not None:
            if existing.state_fingerprint != planning_state_fingerprint(state):
                raise V2PlanOrchestrationError(
                    "Planning state changed since the existing V2 plan was generated."
                )
            return load_persisted_plan_v2(session, existing.id)
        _validate_state(state)
        if state.mode is ClearMode.REGULAR:
            proposal = generate_regular_plan_v2(state)
            persisted = persist_regular_plan_v2(session, state, proposal, actor_id)
        elif state.mode is ClearMode.SPLIT:
            proposal = generate_split_plan_v2(state)
            persisted = persist_split_plan_v2(session, state, proposal, actor_id)
        else:
            raise V2PlanOrchestrationError(f"Unsupported weekly planning mode: {state.mode!r}")
        session.commit()
        return load_persisted_plan_v2(session, persisted.plan_id)
    except (PlanningStateError, V2PlanPersistenceError) as exc:
        session.rollback()
        raise V2PlanOrchestrationError(str(exc)) from exc
    except V2PlanOrchestrationError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise


def _validate_state(state):
    if state.week_status is ReclearWorkflowState.CLOSED:
        raise V2PlanOrchestrationError("Closed weeks cannot create a new loot plan.")
    if not state.mains:
        raise V2PlanOrchestrationError("A weekly V2 plan requires active Main characters.")
    if state.mode is ClearMode.SPLIT and len(state.alts) != len(state.mains):
        raise V2PlanOrchestrationError("Split planning requires one active Alt per Main.")
    if not state.hierarchy:
        raise V2PlanOrchestrationError("A weekly hierarchy snapshot is required for V2 planning.")
    if any(character.needs is None for character in (*state.mains, *state.alts)):
        raise V2PlanOrchestrationError("Every planning character requires a valid V2 needs state.")
