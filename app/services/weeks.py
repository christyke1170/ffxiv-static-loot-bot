"""Configurable weekly reset boundaries and hierarchy snapshots."""

from dataclasses import dataclass
from datetime import date, timedelta

from app.models import JobHierarchy, ReclearWeek, WeeklyHierarchySnapshotEntry


@dataclass(frozen=True, slots=True)
class ResetPeriodPolicy:
    """Map a date to its containing weekly period (Monday=0)."""

    reset_weekday: int = 1

    def week_start(self, value: date) -> date:
        days_since_reset = (value.weekday() - self.reset_weekday) % 7
        return value - timedelta(days=days_since_reset)


def snapshot_hierarchy(week: ReclearWeek, hierarchy: JobHierarchy) -> None:
    """Copy hierarchy entries so later hierarchy edits cannot alter this week."""
    week.source_hierarchy = hierarchy
    week.hierarchy_snapshot[:] = [
        WeeklyHierarchySnapshotEntry(
            job=entry.job, position=entry.position, job_abbreviation=entry.job.abbreviation
        )
        for entry in hierarchy.entries
    ]
