"""One pass over the desk YAML for a single print.

The auto overlay runs first. With em_regime on, the current EM $ picks the
same row as the first-fly credit table, and a later reading can widen it
again, until cookies. Cookies turns on under the EM% cut once enough flies
are open, and then stays for the session. Otherwise the first-fly credit
band cannot widen. Picks that have turned on can tighten the regime. They
cannot widen it. A clock pick stays on the tightest grid reached, from
±20/25 through ±15/20, ±10/15, ±7.5/10, to ±2.5/5. An EM or EM% pick follows
the current reading and turns off when the reading is back above its level.
Then the 0.40-put wing width. The book regime and stop are updated in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from iron_fly_strategy.book import FlyBook
from iron_fly_strategy.rules import scale_mode, wings_for_clock
from iron_fly_strategy.spec import DeskSpec

_WIDTH = {"s1": 1, "s2": 2, "s3": 3, "s4": 4}
# Lower is tighter. Cookies and the ladder are both the ±2.5/5 grid.
_TIGHT = {"cookies": 0, "ladder": 0, "s1": 1, "s2": 2, "s3": 3, "s4": 4}
_WINGS_FROM = time(9, 35)
_PUT_TARGET = 0.40
# Open EM is the 9:30:00–9:30:30 average and is published on the next print.
_EM_FROM = time(9, 30, 30)


@dataclass(frozen=True)
class ConfigState:
    regime: str
    wings: str
    stop: int
    f1_at: time | None
    latest_wings: str = ""
    regime_latch: str = ""


def apply_config_state(
    spec: DeskSpec,
    book: FlyBook,
    *,
    spot: float,
    em_pct: float | None,
    wall: time,
    wing_width: float | None,
    wings: str,
    em_now: float | None = None,
    wing_mid: float | None = None,
    latest_wings: str | None = None,
    em_pct_low: float | None = None,
    em_low: float | None = None,
    regime_latch: str | None = None,
) -> ConfigState:
    """Regime, wings, stop, and F1 for this SPX print. `spot` is that print.

    `latest_wings` is the narrowest width already chosen. This pass can narrow
    it. It cannot go wider, so a 20-wide latch stays 20 when the 0.40 put
    reads wide again.

    An EM% pick is on only while the current EM% is below its level. An EM
    pick is on only while the current EM is below its level. A dip that has
    already recovered does not keep the pick on.

    `regime_latch` is the tightest regime from the credit overlay or a clock
    pick, and cookies once that grid has turned on. An EM pick can tighten
    this print. It does not stick after the reading recovers. A wider pick
    cannot leave cookies or a clock pick's grid. em_regime does not use that
    latch for the credit-table rows: the current EM $ sets the row, and the
    next print may widen it, until cookies has latched.
    """
    del spot
    del em_pct_low, em_low
    book.regime = spec.regime
    book.stop_pad = spec.stop
    width = wings
    # Use Config / desk:auto run the overlay; flat YAML has no desk: and defaults auto.
    em_follow = _trading_auto(spec) and _overlay(spec, book, em_pct=em_pct, wall=wall, em_now=em_now)
    if book.regime == "cookies" or regime_latch == "cookies":
        book.regime = "cookies"
        held = "cookies"
        em_follow = False
    elif em_follow:
        held = ""
    else:
        held = _tighter(book.regime, regime_latch)
        book.regime = held
    for pick in spec.picks:
        if not pick_is_on(
            pick,
            wall=wall,
            em_pct=em_pct,
            em_now=em_now,
        ):
            continue
        if "regime" in pick:
            book.regime = _tighter(book.regime, str(pick["regime"]))
            if "at" in pick:
                held = _tighter(held, book.regime)
        if "stop" in pick:
            book.stop_pad = int(pick["stop"])
        if "wings" in pick:
            width = str(pick["wings"])
    width = _not_wider(latest_wings, width)
    if _trading_auto(spec) and spec.auto.get("follow_wings") and wall >= _WINGS_FROM:
        width = _follow_width(
            wings=width,
            wing_width=wing_width,
            wing_mid=wing_mid,
            wall=wall,
            steps=_steps(spec),
        )
        width = _not_wider(latest_wings, width)
    return ConfigState(
        regime=book.regime,
        wings=width,
        stop=int(book.stop_pad),
        f1_at=_f1_at(spec, book, em_pct, wall),
        latest_wings=width,
        regime_latch=held if em_follow else (held or book.regime),
    )


def slippage_now(
    spec: DeskSpec,
    *,
    wall: time,
    em_pct: float | None = None,
    em_now: float | None = None,
) -> float:
    """Desk slippage, replaced by each pick that is on and sets its own."""
    value = float(spec.slippage)
    for pick in spec.picks:
        if "slippage" not in pick:
            continue
        if not pick_is_on(pick, wall=wall, em_pct=em_pct, em_now=em_now):
            continue
        value = float(pick["slippage"])
    return value


def pick_is_on(
    pick: dict[str, object],
    *,
    wall: time,
    em_pct: float | None = None,
    em_now: float | None = None,
    em_pct_low: float | None = None,
    em_low: float | None = None,
) -> bool:
    """True while a pick applies. A clock pick stays on after its time.

    An EM% pick applies only while EM% is below its level. An EM pick applies
    only while EM is below its level. Both wait until the open EM has latched.
    """
    del em_pct_low, em_low
    if "at" in pick:
        return wall >= time.fromisoformat(str(pick["at"]))
    if not _em_ready(wall):
        return False
    if "at_em_pct" in pick:
        return em_pct is not None and float(em_pct) < float(pick["at_em_pct"])
    if "at_em" in pick:
        return em_now is not None and float(em_now) < float(pick["at_em"])
    return False


def _em_ready(wall: time) -> bool:
    """True once the open EM is published, on the first print after 9:30:30."""
    return wall > _EM_FROM


def _tighter(held: str | None, proposed: str | None) -> str:
    """Keep the tighter grid. An equal grid stays. An empty side leaves the other."""
    if not proposed:
        return held or ""
    if not held or held not in _TIGHT:
        return proposed if proposed in _TIGHT else (held or proposed)
    if proposed not in _TIGHT:
        return held
    if _TIGHT[proposed] < _TIGHT[held]:
        return proposed
    return held


def _overlay(
    spec: DeskSpec,
    book: FlyBook,
    *,
    em_pct: float | None,
    wall: time,
    em_now: float | None,
) -> bool:
    """Set the overlay regime. True when current EM $ owns this print.

    An owned print keeps the EM row only for this reading. The caller does
    not fold it into the tighten latch, so the next print can widen. Cookies
    is not owned: the caller keeps it after EM% recovers. Credit stays
    capped by the desk regime.
    """
    auto = spec.auto
    if not _em_ready(wall):
        em_pct = None
        em_now = None
    limit = auto.get("cookies_below")
    need = int(auto.get("cookies_min_flies") or 0)
    if limit is not None and em_pct is not None and float(em_pct) < float(limit) and len(book.active()) >= need:
        book.regime = "cookies"
        return False
    if auto.get("em_regime") and em_now is not None:
        book.regime = scale_mode(float(em_now)).name
        return True
    if not auto.get("credit_regime"):
        return False
    credit = book.regime_credit
    if credit is None:
        return False
    book.regime = _clamp(scale_mode(float(credit)).name, _regime_cap(spec))
    return False


def _regime_cap(spec: DeskSpec) -> str | None:
    named = spec.auto.get("regime_max")
    if isinstance(named, str) and named in _WIDTH:
        return named
    if spec.regime in _WIDTH:
        return spec.regime
    return None


def _clamp(wanted: str, cap: str | None) -> str:
    if cap is None or wanted not in _WIDTH or cap not in _WIDTH:
        return wanted
    if _WIDTH[wanted] > _WIDTH[cap]:
        return cap
    return wanted


def _steps(spec: DeskSpec) -> tuple[int, ...]:
    raw = spec.auto.get("wing_steps")
    if isinstance(raw, list) and raw:
        return tuple(int(step) for step in raw)
    return (50, 40, 30, 25, 20)


def _not_wider(held: str | None, chosen: str) -> str:
    """Keep the narrower width. An empty latch leaves the choice alone."""
    if not held:
        return chosen
    try:
        old = int(held)
        new = int(chosen)
    except (TypeError, ValueError):
        return chosen
    if old > 0 and new > 0 and old < new:
        return str(old)
    return chosen


def _follow_width(
    *,
    wings: str,
    wing_width: float | None,
    wing_mid: float | None,
    wall: time,
    steps: tuple[int, ...],
) -> str:
    """Snap the 0.40 put width. Never widen past the width already latched."""
    if wing_width is None or not steps:
        return wings
    if wing_mid is not None and wing_mid < _PUT_TARGET:
        return wings
    snapped = _snap(float(wing_width), steps)
    return str(min(snapped, _latched(wings, wall, steps)))


def _snap(width: float, steps: tuple[int, ...]) -> int:
    lo, hi = min(steps), max(steps)
    clamped = min(float(hi), max(float(lo), width))
    return min(steps, key=lambda step: (abs(step - clamped), -step))


def _latched(wings: str, wall: time, steps: tuple[int, ...]) -> int:
    try:
        value = int(wings)
    except (TypeError, ValueError):
        value = 0
    if value > 0:
        return value
    return wings_for_clock(wall) if wall is not None else max(steps)


def _trading_auto(spec: DeskSpec) -> bool:
    """True for desk:auto (flat YAML defaults to auto). desk:manual skips overlays."""
    return spec.desk == "auto"


def _f1_at(spec: DeskSpec, book: FlyBook, em_pct: float | None, wall: time) -> time | None:
    below = spec.auto.get("f1_below")
    # Closed blotter rows do not count — only an active fly skips the EM hold.
    if not _trading_auto(spec) or below is None or book.active():
        return spec.f1_at
    if not _em_ready(wall):
        em_pct = None
    if em_pct is None or float(em_pct) >= float(below):
        return None
    if spec.f1_at is not None and wall < spec.f1_at:
        return None
    return spec.f1_at or wall
