"""Idempotent durable job-hierarchy initialization."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.loot_planning_config import REGULAR_JOB_PRIORITY
from app.models import Job, JobHierarchy, JobHierarchyEntry, Static


def ensure_default_hierarchy(session: Session, static: Static) -> JobHierarchy | None:
    """Create the exact default once and never modify existing hierarchy data."""
    if session.scalar(select(JobHierarchy.id).where(JobHierarchy.static_id == static.id)):
        return None
    available = {job.abbreviation.upper(): job for job in session.scalars(select(Job))}
    missing = [code for code in REGULAR_JOB_PRIORITY if code not in available]
    if missing:
        return None
    hierarchy = JobHierarchy(static=static, version=1, active_marker=True, name="Default")
    hierarchy.entries = [
        JobHierarchyEntry(job=available[code], position=position)
        for position, code in enumerate(REGULAR_JOB_PRIORITY, 1)
    ]
    session.add(hierarchy)
    session.flush()
    return hierarchy


def bootstrap_default_hierarchies(session: Session) -> int:
    """Initialize every hierarchy-less static and return the number created."""
    created = 0
    for static in session.scalars(select(Static).order_by(Static.id)):
        created += ensure_default_hierarchy(session, static) is not None
    return created
