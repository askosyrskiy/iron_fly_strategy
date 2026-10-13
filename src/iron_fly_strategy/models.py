from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SessionMode(StrEnum):
    MANUAL = "manual"
    CONFIRM = "confirm"
    AUTO = "auto"


class IntentKind(StrEnum):
    ADD = "add"
    CLOSE = "close"
    FLATTEN = "flatten"


@dataclass(frozen=True)
class Quote:
    symbol: str
    last: float
    bid: float | None = None
    ask: float | None = None
    ts: datetime | None = None


@dataclass(frozen=True)
class Intent:
    kind: IntentKind
    reason: str
    payload: dict[str, object] = field(default_factory=dict)
