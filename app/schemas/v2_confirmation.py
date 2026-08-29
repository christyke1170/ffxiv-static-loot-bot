"""Immutable neutral V2 confirmation and effect-ledger values."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class V2ConfirmationState:
    confirmation_id: int
    assignment_id: int
    resource_key: str
    action: str
    success: bool
    recipient_id: int | None
    quantity: int
    actor_id: int | None
    created_at: datetime | None
    note: str | None


@dataclass(frozen=True, slots=True)
class V2EffectState:
    effect_id: int
    confirmation_id: int
    recipient_id: int
    slot_key: str
    resulting_category: str
    before_category: str | None
    after_category: str | None
    quantity_delta: int


@dataclass(frozen=True, slots=True)
class V2ConfirmationReadback:
    assignment_id: int
    confirmations: tuple[V2ConfirmationState, ...]
    effects: tuple[V2EffectState, ...]
    balances: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class V2CorrectionState:
    correction_id: int
    confirmation_id: int
    correction_type: str
    corrected_success: bool | None
    actor_id: int
    reason: str
    created_at: datetime | None
