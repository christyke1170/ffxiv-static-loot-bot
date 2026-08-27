"""Transport-independent weekly reclear and loot-board snapshots."""

from dataclasses import dataclass
from datetime import date

from app.models import (
    ClearMode,
    ConfirmationQuestion,
    LootAssignmentState,
    ReclearWorkflowState,
)


@dataclass(frozen=True, slots=True)
class RosterEntry:
    member_id: int
    member_name: str
    character_id: int
    character_name: str
    kind: str


@dataclass(frozen=True, slots=True)
class GroupRoster:
    group_id: int
    group_number: int
    entries: tuple[RosterEntry, ...]


@dataclass(frozen=True, slots=True)
class ReclearStatus:
    week_id: int
    static_id: int
    static_name: str
    guild_id: int
    week_start: date
    mode: ClearMode
    workflow_state: ReclearWorkflowState
    tier_name: str
    hierarchy: tuple[str, ...]
    groups: tuple[GroupRoster, ...]
    completions: tuple[tuple[int, str], ...]
    plan_state: str
    confirmation_summary: str
    distribution_errors: int
    can_close: bool


@dataclass(frozen=True, slots=True)
class LootBoardRow:
    assignment_id: int
    floor_id: int
    floor_number: int
    floor_name: str
    group_number: int
    drop_name: str
    instance: int
    recipient: str
    backup: str
    status: LootAssignmentState
    intended_slot: str
    intended_item: str
    suggested_recipient: str
    final_recipient: str
    hierarchy_position: int | None
    reason: str
    base_tome_owned: bool | None
    confirmations: tuple[tuple[ConfirmationQuestion, bool, str], ...]
    distribution_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LootBoard:
    week_id: int
    static_id: int
    static_name: str
    guild_id: int
    week_start: date
    rows: tuple[LootBoardRow, ...]
    member_discord_user_ids: frozenset[int]
