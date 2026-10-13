from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass
class FlyRow:
    slot: int
    strike: float | None
    status: Literal["empty", "active", "closed"]
    put_wing: int | None = None
    call_wing: int | None = None
    sto: float | None = None
    btc_actual: float | None = None
    btc_est: float | None = None
    net: float | None = None
    max_loss: float | None = None
    origin: str = ""


@dataclass
class CaptainsLog:
    last_price: float
    watch_spot: float
    watch_add: float
    watch_var: float
    next_spot: float
    next_add: float
    next_var: float
    atm_strike: float
    wings: int
    max_opp: float
    remaining_opp: float
    used_close_pct: float
    btc_remaining_pct: float
    auto_refresh: bool
    flies: list[FlyRow]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


BLOTTER_MAX = 20


def empty_blotter(slots: int = 1) -> list[FlyRow]:
    n = max(1, min(int(slots), BLOTTER_MAX))
    return [FlyRow(slot=i, strike=None, status="empty") for i in range(1, n + 1)]
