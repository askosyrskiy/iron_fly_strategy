"""Hybrid v0 scale rules. Pure functions — no I/O."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ScaleMode:
    name: str
    move_points: float
    offset_points: float


S1 = ScaleMode("s1", 7.5, 10.0)
S2 = ScaleMode("s2", 10.0, 15.0)
S3 = ScaleMode("s3", 15.0, 20.0)
S4 = ScaleMode("s4", 20.0, 25.0)
LADDER = ScaleMode("ladder", 2.5, 5.0)
COOKIES = ScaleMode("cookies", 2.5, 5.0)
LADDER_AFTER = time(13, 0)  # 1:00 ET
REGIME_MODES = {
    "s1": S1,
    "s2": S2,
    "s3": S3,
    "s4": S4,
    "ladder": LADDER,
    "cookies": COOKIES,
}
REGIME_LABELS = {
    "s1": "±7.5 / 10",
    "s2": "±10 / 15",
    "s3": "±15 / 20",
    "s4": "±20 / 25",
    "ladder": "±2.5 / 5",
    "cookies": "Cookies ±2.5 / 5",
}
_ET = ZoneInfo("America/New_York")


def scale_mode(first_credit: float) -> ScaleMode:
    """Latch from the first fill credit (Captain's Log First-fly credit)."""
    if first_credit < 15:
        return S1
    if first_credit <= 19:
        return S2
    if first_credit < 24.90:
        return S3
    return S4


def parse_regime(raw: str | None) -> str:
    name = (raw or "s1").strip().lower()
    if name == "auto" or name in REGIME_MODES:
        return name
    return "s1"


def parse_desk_regime(raw: str | None) -> str:
    """Captain's Log select: leftover Auto cookies stay on s1."""
    name = parse_regime(raw)
    return "s1" if name == "auto" else name


def resolve_mode(regime: str, first_credit: float | None, clock: time) -> ScaleMode:
    """Forced select wins; Auto is first-fill credit until 1:00 ET, then ladder."""
    picked = REGIME_MODES.get(parse_regime(regime))
    if picked is not None:
        return picked
    if clock >= LADDER_AFTER:
        return LADDER
    if first_credit is None:
        return S1
    return scale_mode(first_credit)


def round_to_5(spot: float) -> float:
    return float(5 * round(spot / 5))


def add_short_strike(anchor: float, direction: int, mode: ScaleMode) -> float:
    if direction not in (-1, 1):
        raise ValueError("direction must be +1 or -1")
    return round_to_5(anchor + direction * mode.offset_points)


@dataclass(frozen=True)
class AddTarget:
    """One Watch / Next Add: trigger, short to open, and side from the claiming fly."""

    watch: float
    strike: float
    side: str


def next_adds(
    spot: float,
    regime: str,
    shorts: Sequence[float],
) -> tuple[AddTarget | None, AddTarget | None]:
    """Next Add above last and next Add below last.

    ``regime`` picks the grid (s1–s4 outer edges, Cookies/Ladder ±2.5/5 neighbors).
    Open shorts are skipped. Cookies keep at most two Adds: the nearest watch at
    or above last, and the nearest watch below last. One fly still returns both
    edge Adds. Empty book is ATM ± the grid.
    """
    mode = REGIME_MODES.get(parse_desk_regime(regime), S1)
    open_shorts = sorted({float(strike) for strike in shorts})
    if not open_shorts:
        atm = round_to_5(spot)
        return (
            AddTarget(
                atm + mode.move_points,
                add_short_strike(atm, 1, mode),
                "up",
            ),
            AddTarget(
                atm - mode.move_points,
                add_short_strike(atm, -1, mode),
                "down",
            ),
        )
    if mode.name not in ("cookies", "ladder"):
        lo, hi = open_shorts[0], open_shorts[-1]
        return (
            AddTarget(
                hi + mode.move_points,
                add_short_strike(hi, 1, mode),
                "up",
            ),
            AddTarget(
                lo - mode.move_points,
                add_short_strike(lo, -1, mode),
                "down",
            ),
        )
    candidates = _vacant_neighbors(open_shorts, mode, spot=spot, unique=mode.name == "ladder")
    return _pair_around_last(candidates, spot)


def _vacant_neighbors(
    shorts: list[float],
    mode: ScaleMode,
    *,
    spot: float,
    unique: bool,
) -> list[AddTarget]:
    open_set = set(shorts)
    origins: dict[float, list[tuple[float, int]]] = {}
    for strike in shorts:
        for direction in (1, -1):
            add = add_short_strike(strike, direction, mode)
            if add in open_set:
                continue
            origins.setdefault(add, []).append((strike, direction))
    out: list[AddTarget] = []
    for add, claimants in origins.items():
        picked = claimants
        if unique:
            picked = [min(claimants, key=lambda item: abs(item[0] - spot))]
        for strike, direction in picked:
            side = "up" if direction == 1 else "down"
            out.append(AddTarget(strike + direction * mode.move_points, add, side))
    return out


def _pair_around_last(targets: list[AddTarget], spot: float) -> tuple[AddTarget | None, AddTarget | None]:
    if not targets:
        return None, None
    if len(targets) <= 2:
        ups = [item for item in targets if item.side == "up"]
        downs = [item for item in targets if item.side == "down"]
        up = max(ups, key=lambda item: item.watch) if ups else None
        down = min(downs, key=lambda item: item.watch) if downs else None
        return up, down
    above = [item for item in targets if item.watch >= spot]
    below = [item for item in targets if item.watch < spot]
    up = min(above, key=lambda item: item.watch) if above else None
    down = max(below, key=lambda item: item.watch) if below else None
    return up, down


def holes_between(lo: float, hi: float) -> list[float]:
    """5-point shorts strictly inside (lo, hi)."""
    if hi - lo < 10:
        return []
    strike = lo + 5
    out: list[float] = []
    while strike < hi:
        out.append(strike)
        strike += 5
    return out


def max_loss_per_share(wing_width: float, credit: float) -> float:
    return wing_width - credit


def fly_expiration_pnl(
    spot: float,
    strike: float,
    credit: float,
    put_wing: float,
    call_wing: float,
) -> float:
    """Per-share PnL at expiration: credit minus short intrinsic, plus long wings."""
    short = max(strike - spot, 0.0) + max(spot - strike, 0.0)
    long_put = max((strike - put_wing) - spot, 0.0)
    long_call = max(spot - (strike + call_wing), 0.0)
    return credit - short + long_put + long_call


def open_flies_max_profit(
    flies: list[tuple[float, float, float, float]],
) -> tuple[float | None, float | None, float | None]:
    """Max expiration PnL per share for open flies, and the pin (lo, hi) where it hits.

    Each fly is ``(short, credit, put_wing, call_wing)``. Piecewise-linear, so the
    max is at a short or a wing. With t>1 shorts you cannot collect every credit.
    """
    if not flies:
        return None, None, None
    knots: set[float] = set()
    for strike, _credit, put_wing, call_wing in flies:
        knots.add(float(strike))
        knots.add(float(strike) - float(put_wing))
        knots.add(float(strike) + float(call_wing))
    scored: list[tuple[float, float]] = []
    for spot in sorted(knots):
        total = sum(
            fly_expiration_pnl(spot, strike, credit, put_wing, call_wing)
            for strike, credit, put_wing, call_wing in flies
        )
        scored.append((spot, total))
    best = max(item[1] for item in scored)
    winners = [spot for spot, pnl in scored if abs(pnl - best) < 0.005]
    return round(best, 2), winners[0], winners[-1]


def max_profit_view(
    flies: list[tuple[float, float, float, float]],
    closed_nets: list[float | None] | None = None,
) -> dict[str, object]:
    """Captain's Log fields: dollars, pin, open count. Empty book is zeros/None.

    Closed losses (per share, negative nets) are subtracted from that dollar max.
    A closed winner stays in P&L and does not raise the max.
    """
    credit, lo, hi = open_flies_max_profit(flies)
    dollars = None if credit is None else round(credit * 100, 0)
    loss = sum(float(net) for net in (closed_nets or []) if net is not None and float(net) < 0)
    if loss:
        dollars = round((0.0 if dollars is None else dollars) + loss * 100, 0)
    pin_hi = None
    if lo is not None and hi is not None and hi != lo:
        pin_hi = hi
    return {
        "max_profit": dollars,
        "max_profit_credit": credit,
        "max_profit_at": lo,
        "max_profit_at_hi": pin_hi,
        "max_profit_n": len(flies),
    }


def breakevens(short_strike: float, credit: float) -> tuple[float, float]:
    return short_strike - credit, short_strike + credit


STOP_PAD_MAX = 20
STOP_PADS = tuple(range(STOP_PAD_MAX + 1))


def book_clock(clock: time | datetime) -> time:
    """Naive times are ET. Aware datetimes convert to ET wall clock."""
    if isinstance(clock, datetime):
        ts = clock if clock.tzinfo is not None else clock.replace(tzinfo=_ET)
        return ts.astimezone(_ET).time()
    return clock


def valid_stop_pad(pad: int) -> bool:
    """True for an integer additional stop from 0 through STOP_PAD_MAX."""
    return isinstance(pad, int) and not isinstance(pad, bool) and 0 <= pad <= STOP_PAD_MAX


def parse_stop_pad(raw: object) -> int:
    """Additional stop points beyond K ± C. YAML and the sheet use the same range."""
    text = str(raw if raw is not None else "0").strip().lower().lstrip("+")
    try:
        pad = int(text)
    except (TypeError, ValueError):
        return 0
    return pad if valid_stop_pad(pad) else 0


def drop_levels(strike: float, credit: float, pad: float = 0.0) -> tuple[float, float]:
    """Down / up Drop watches. Pad pushes each point further from the short."""
    lo, hi = breakevens(strike, credit)
    return lo - pad, hi + pad


def past_breakeven(short_strike: float, credit: float, spot: float) -> bool:
    """True when SPX is at or beyond that fly's expiration BE (`K ± C`)."""
    lo, hi = breakevens(short_strike, credit)
    return spot <= lo or spot >= hi


TAG_TOLERANCE = 3.0


def tag_band(lo: float, hi: float) -> float:
    """Dale 1–3 point early tag. 5-wide Oreo stays exact so the cream is not a drop."""
    width = hi - lo
    if width <= 5:
        return 0.0
    return min(TAG_TOLERANCE, width / 2.0 - 0.5)


def credit_drops(flies: list[tuple[float, float]], spot: float, pad: float = 0.0) -> list[float]:
    """Every fly whose drop level has been reached, most violated first."""
    past: list[tuple[float, float]] = []
    for strike, credit in flies:
        lo, hi = drop_levels(strike, credit, pad)
        if spot >= hi:
            past.append((spot - hi, strike))
        elif spot <= lo:
            past.append((lo - spot, strike))
    past.sort(key=lambda item: item[0], reverse=True)
    return [strike for _, strike in past]


def credit_drop(flies: list[tuple[float, float]], spot: float, pad: float = 0.0) -> float | None:
    """The most violated fly whose drop level has been reached."""
    drops = credit_drops(flies, spot, pad)
    return drops[0] if drops else None


def wings_for_clock(clock: time) -> int:
    return 20 if clock >= time(15, 0) else 50


def next_fly_strip(
    spot: float,
    clock: time,
    mode: ScaleMode | None = None,
) -> dict[str, float | int]:
    """Captain's Log strip from live SPX. Next fly is the ATM 5-point."""
    mode = mode or S1
    atm = round_to_5(spot)
    watch = atm + mode.move_points
    nxt = atm - mode.move_points
    return {
        "last_price": spot,
        "atm_strike": atm,
        "wings": wings_for_clock(clock),
        "watch_spot": watch,
        "watch_add": add_short_strike(atm, 1, mode),
        "watch_var": round(watch - spot, 2),
        "next_spot": nxt,
        "next_add": add_short_strike(atm, -1, mode),
        "next_var": round(nxt - spot, 2),
    }


WATCH_APPROACH = 0.50
ADD_SKIP_POINTS = 5.0


def watch_just_crossed(side: str, watch: float, spot: float, prev_spot: float | None) -> bool:
    """True only when last moved through the watch this tick (not already past it)."""
    if prev_spot is None:
        return False
    if side == "up":
        return prev_spot < watch <= spot
    if side == "down":
        return prev_spot > watch >= spot
    return False


def tagged_add_strike(
    rows: list[dict[str, Any]],
    *,
    spot: float,
    shorts: set[float],
    prev_spot: float | None,
    prev_mode: str | None,
    mode: str,
) -> float | None:
    """Add short last newly crossed.

    A regime change with no real tape move arms the matrix and does not fire.
    Cookies opens its own ATM fly once, then uses this same fresh-cross rule.
    If last jumped ``ADD_SKIP_POINTS`` or more, send only the latest tag
    (nearest last) and skip the rungs the gap passed.
    """
    if prev_spot is None:
        return None
    move = abs(spot - prev_spot)
    rebuilt = prev_mode is not None and prev_mode != mode
    if move >= ADD_SKIP_POINTS:
        return _nearest_through_add(rows, spot=spot, shorts=shorts)
    if rebuilt:
        return None
    fresh = _fresh_cross_add(rows, spot=spot, shorts=shorts, prev_spot=prev_spot)
    if fresh is not None:
        return fresh
    return _held_through_add(rows, spot=spot, shorts=shorts)


def _nearest_through_add(
    rows: list[dict[str, Any]],
    *,
    spot: float,
    shorts: set[float],
) -> float | None:
    tagged: list[float] = []
    for row in rows:
        if row.get("kind") != "add" or not row.get("enabled"):
            continue
        strike = row.get("strike")
        if strike is None:
            continue
        target = float(strike)
        if target in shorts:
            continue
        watch = float(row["watch"])
        side = str(row.get("side") or "")
        if side == "up" and spot >= watch:
            tagged.append(target)
        elif side == "down" and spot <= watch:
            tagged.append(target)
    if not tagged:
        return None
    return min(tagged, key=lambda strike: abs(strike - spot))


def _held_through_add(
    rows: list[dict[str, Any]],
    *,
    spot: float,
    shorts: set[float],
) -> float | None:
    """The live add whose watch price is already through, closest first.

    A drop on the crossing second used to consume that decision. The next
    print is no longer a fresh cross, so the strike would never open.
    """
    held: list[tuple[float, float]] = []
    for row in rows:
        if row.get("kind") != "add" or not row.get("enabled"):
            continue
        strike = row.get("strike")
        if strike is None:
            continue
        target = float(strike)
        if target in shorts:
            continue
        watch = float(row["watch"])
        side = str(row.get("side") or "")
        if side == "up" and spot >= watch:
            held.append((spot - watch, target))
        elif side == "down" and spot <= watch:
            held.append((watch - spot, target))
    if not held:
        return None
    held.sort(key=lambda item: item[0])
    return held[0][1]


def _fresh_cross_add(
    rows: list[dict[str, Any]],
    *,
    spot: float,
    shorts: set[float],
    prev_spot: float,
) -> float | None:
    crossed: list[tuple[float, float]] = []
    for row in rows:
        if row.get("kind") != "add" or not row.get("enabled"):
            continue
        strike = row.get("strike")
        if strike is None:
            continue
        target = float(strike)
        if target in shorts:
            continue
        watch = float(row["watch"])
        side = str(row.get("side") or "")
        if not watch_just_crossed(side, watch, spot, prev_spot):
            continue
        if side == "up":
            crossed.append((spot - watch, target))
        else:
            crossed.append((watch - spot, target))
    if not crossed:
        return None
    crossed.sort(key=lambda item: item[0], reverse=True)
    return crossed[0][1]


def add_watch_alerts(
    ladder: list[dict[str, Any]],
    spot: float,
    band: float = WATCH_APPROACH,
) -> list[dict[str, Any]]:
    """Flash within 50 cents of an Add or Drop watch; solid after SPX has crossed it."""
    out: list[dict[str, Any]] = []
    for row in ladder:
        if row.get("kind") not in ("add", "drop"):
            continue
        watch = float(row["watch"])
        side = str(row.get("side") or "")
        gap = round(watch - spot, 2)
        if side == "up":
            if gap <= 0:
                state = "crossed"
            elif gap <= band:
                state = "approach"
            else:
                continue
        elif side == "down":
            if gap >= 0:
                state = "crossed"
            elif -gap <= band:
                state = "approach"
            else:
                continue
        else:
            continue
        strike = row.get("strike")
        action = "Add" if row.get("kind") == "add" else "Drop"
        label = f"{side} watch {watch:,.1f}"
        if strike is not None:
            label += f" — {action} {int(strike)}"
        if state == "approach":
            text = f"Approaching {label} ({abs(gap):.2f} away)"
        else:
            text = f"Crossed {label}"
        out.append(
            {
                "side": side,
                "state": state,
                "watch": watch,
                "strike": strike,
                "distance": abs(gap),
                "text": text,
            }
        )
    return out
