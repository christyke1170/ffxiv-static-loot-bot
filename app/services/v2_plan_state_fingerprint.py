"""Deterministic fingerprints for immutable V2 planning inputs."""

import json
from dataclasses import asdict, is_dataclass
from datetime import date
from enum import Enum
from hashlib import sha256


def planning_state_fingerprint(state) -> str:
    """Hash every immutable planning input without exposing ORM values."""
    # Active-plan metadata is derived from persistence and must not make an
    # otherwise identical source state stale after the first successful write.
    data = _primitive(state)
    if isinstance(data, dict):
        data.pop("active_plan", None)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _primitive(value):
    if is_dataclass(value):
        return {key: _primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_primitive(item) for item in value]
    if isinstance(value, list):
        return [_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    return value
