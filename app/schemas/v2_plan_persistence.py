"""Immutable readback values for neutral V2 proposal persistence."""

from dataclasses import dataclass

from app.schemas.regular_planning_v2 import RegularPlanProposal
from app.schemas.split_planning_v2 import SplitPlanProposal


@dataclass(frozen=True, slots=True)
class PersistedV2Plan:
    plan_id: int
    proposal: RegularPlanProposal | SplitPlanProposal

    def __getattr__(self, name: str):
        return getattr(self.proposal, name)
