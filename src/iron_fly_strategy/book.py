"""Open-fly book + Captain's Log ladder. Open/close policy lives in dale_*.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from iron_fly_strategy.models import Intent, IntentKind, Quote
from iron_fly_strategy.rules import (
    AddTarget,
    ScaleMode,
    add_short_strike,
    add_watch_alerts,
    book_clock,
    credit_drop,
    drop_levels,
    holes_between,
    max_loss_per_share,
    max_profit_view,
    next_adds,
    next_fly_strip,
    resolve_mode,
    round_to_5,
    scale_mode,
    wings_for_clock,
)
from iron_fly_strategy.view import (
    BLOTTER_MAX,
    CaptainsLog,
    FlyRow,
    empty_blotter,
)


@dataclass
class OpenFly:
    strike: float
    credit: float
    wings: int
    opened_at: datetime
    status: str = "active"
    close_debit: float | None = None
    closed_at: datetime | None = None
    origin: str = ""

    @property
    def net(self) -> float | None:
        if self.status == "closed" and self.close_debit is not None:
            return round(self.credit - self.close_debit, 2)
        return None


@dataclass
class Watch:
    trigger: float
    add_strike: float


class FlyBook:
    def __init__(self) -> None:
        self.flies: list[OpenFly] = []
        self.spot: float | None = None
        self._first_credit: float | None = None
        self._last_credit: float | None = None
        self.regime: str = "s1"
        self.stop_pad: int = 0

    def open_fly(
        self,
        strike: float,
        credit: float,
        wings: int,
        ts: datetime,
        origin: str = "",
    ) -> OpenFly:
        fly = OpenFly(strike=strike, credit=credit, wings=wings, opened_at=ts, origin=origin)
        self.flies.append(fly)
        # First-fly credit bands are for 50-wide; narrower adds do not re-latch.
        if wings == 50:
            if self._first_credit is None:
                self._first_credit = credit
            self._last_credit = credit
        return fly

    @property
    def regime_credit(self) -> float | None:
        """Latest 50-wide open credit for the First-fly grid; else the first 50-wide."""
        if self._last_credit is not None:
            return self._last_credit
        return self._first_credit

    def close_fly(self, strike: float, debit: float, ts: datetime) -> OpenFly:
        fly = self._active(strike)
        fly.status = "closed"
        fly.close_debit = debit
        fly.closed_at = ts
        return fly

    def on_quote(self, quote: Quote) -> None:
        self.spot = quote.last

    def active(self) -> list[OpenFly]:
        return [f for f in self.flies if f.status == "active"]

    def active_strikes(self) -> list[float]:
        return sorted(f.strike for f in self.active())

    @property
    def cookie_opened(self) -> bool:
        """The one Cookies fly for this day, open or already closed."""
        return any(fly.origin == "cookie" for fly in self.flies)

    def mark_cookie(self, strike: float) -> None:
        for fly in self.active():
            if fly.strike == strike:
                fly.origin = "cookie"
                return
        for fly in self.flies:
            if fly.strike == strike:
                fly.origin = "cookie"
                return

    def morning_mode(self) -> ScaleMode | None:
        credit = self.regime_credit
        if credit is None:
            return None
        return scale_mode(credit)

    def mode_at(self, clock: time | datetime) -> ScaleMode:
        return resolve_mode(self.regime, self.regime_credit, book_clock(clock))

    def _edge(self, strike: float, direction: int, mode: ScaleMode) -> Watch:
        """Add watch and short from an open fly. Watch is the latched scenario move."""
        if direction not in (-1, 1):
            raise ValueError("direction must be +1 or -1")
        return Watch(
            strike + direction * mode.move_points,
            add_short_strike(strike, direction, mode),
        )

    def _pair_watches(self, above: AddTarget | None, below: AddTarget | None) -> list[tuple[str, Watch]]:
        out: list[tuple[str, Watch]] = []
        if above is not None:
            out.append((above.side, Watch(above.watch, above.strike)))
        if below is not None:
            out.append((below.side, Watch(below.watch, below.strike)))
        return out

    def _empty_adds(self, spot: float, mode: ScaleMode) -> list[tuple[str, Watch]]:
        return self._pair_watches(*next_adds(spot, mode.name, []))

    def _add_targets(self, shorts: list[float], mode: ScaleMode, spot: float) -> list[tuple[str, Watch]]:
        """Unique add strikes. Ladder watch is from the open short nearest spot."""
        if not shorts:
            return []
        if mode.name != "ladder":
            return self._pair_watches(*next_adds(spot, mode.name, shorts))
        origins: dict[float, list[tuple[float, int]]] = {}
        open_set = set(shorts)
        for strike in shorts:
            for direction in (1, -1):
                add = add_short_strike(strike, direction, mode)
                if add in open_set:
                    continue
                origins.setdefault(add, []).append((strike, direction))
        out: list[tuple[str, Watch]] = []
        for add, claimants in origins.items():
            strike, direction = min(claimants, key=lambda item: abs(item[0] - spot))
            side = "up" if direction == 1 else "down"
            out.append((side, Watch(strike + direction * mode.move_points, add)))
        return out

    def watches(self, spot: float, clock: time | datetime) -> tuple[Watch, Watch]:
        mode = self.mode_at(clock)
        shorts = self.active_strikes()
        if not shorts:
            empty = self._empty_adds(spot, mode)
            return empty[0][1], empty[1][1]
        if mode.name in ("ladder", "cookies"):
            targets = self._add_targets(shorts, mode, spot)
            ups = [watch for side, watch in targets if side == "up"]
            downs = [watch for side, watch in targets if side == "down"]
            if mode.name == "cookies":
                parked = Watch(spot, shorts[0])
                if not ups and not downs:
                    return parked, parked
                up = max(ups, key=lambda item: item.trigger) if ups else parked
                down = min(downs, key=lambda item: item.trigger) if downs else parked
                return up, down
            up = max(ups, key=lambda item: item.trigger) if ups else self._edge(shorts[-1], 1, mode)
            down = min(downs, key=lambda item: item.trigger) if downs else self._edge(shorts[0], -1, mode)
            return up, down
        lo, hi = shorts[0], shorts[-1]
        holes = [h for h in holes_between(lo, hi) if h not in shorts]
        if holes:
            nearest = min(holes, key=lambda h: abs(h - spot))
            return Watch(spot, nearest), Watch(spot, nearest)
        return self._edge(hi, 1, mode), self._edge(lo, -1, mode)

    def log_ladder(self, spot: float, clock: time | datetime) -> list[dict[str, Any]]:
        """Captain's Log rows, watch high to low. Drop is K ± C plus stop_pad."""
        mode = self.mode_at(clock)
        shorts = self.active_strikes()
        targets = self._add_targets(shorts, mode, spot) if shorts else self._empty_adds(spot, mode)

        def row(
            side: str,
            kind: str,
            watch: float,
            strike: float | None,
            which: str,
            *,
            rule: bool = False,
        ) -> dict[str, Any]:
            return {
                "side": side,
                "kind": kind,
                "watch": watch,
                "strike": strike,
                "var": round(watch - spot, 2),
                "which": which,
                "enabled": strike is not None,
                "rule": rule,
            }

        ups = [watch for side, watch in targets if side == "up"]
        downs = [watch for side, watch in targets if side == "down"]
        rows: list[dict[str, Any]] = []
        for i, watch in enumerate(ups):
            which = "watch" if i == 0 else f"add-{int(watch.add_strike)}"
            rows.append(row("up", "add", watch.trigger, watch.add_strike, which))
        for fly in self.active():
            lo_be, hi_be = drop_levels(fly.strike, fly.credit, float(self.stop_pad))
            k = int(fly.strike)
            rows.append(row("up", "drop", hi_be, fly.strike, f"drop-{k}-up"))
            rows.append(row("down", "drop", lo_be, fly.strike, f"drop-{k}-down"))
        for i, watch in enumerate(downs):
            which = "next" if i == len(downs) - 1 else f"add-{int(watch.add_strike)}"
            rows.append(row("down", "add", watch.trigger, watch.add_strike, which))
        rows.sort(
            key=lambda item: (
                -float(item["watch"]),
                abs(float(item["strike"] or 0) - spot),
            )
        )
        for item in rows:
            item["rule"] = False
        for item in rows:
            if float(item["watch"]) < spot:
                item["rule"] = True
                break
        return rows

    def propose(self, spot: float, ts: datetime) -> list[Intent]:
        """Geometry helper (holes / extend). Live auto and backtest use DaleOg.decide."""
        intents: list[Intent] = []
        clock = book_clock(ts)
        up, down = self.watches(spot, clock)
        shorts = self.active_strikes()
        wings = wings_for_clock(clock)

        if not shorts:
            intents.append(
                Intent(
                    IntentKind.ADD,
                    "first ATM fly",
                    {"strike": round_to_5(spot), "wings": wings},
                )
            )
            return intents

        lo, hi = shorts[0], shorts[-1]
        drop = credit_drop(
            [(fly.strike, fly.credit) for fly in self.active()],
            spot,
            float(self.stop_pad),
        )
        if drop is not None:
            return [
                Intent(
                    IntentKind.CLOSE,
                    "expiration breakeven",
                    {"strike": drop},
                )
            ]
        holes = [h for h in holes_between(lo, hi) if h not in shorts]
        if holes:
            strike = min(holes, key=lambda h: abs(h - spot))
            intents.append(
                Intent(
                    IntentKind.ADD,
                    "fill 5-point hole",
                    {"strike": strike, "wings": wings},
                )
            )
        else:
            mode = self.mode_at(clock)
            up, down = self._edge(hi, 1, mode), self._edge(lo, -1, mode)
            if spot <= down.trigger:
                intents.append(
                    Intent(
                        IntentKind.ADD,
                        "extend down",
                        {
                            "strike": down.add_strike,
                            "wings": wings,
                        },
                    )
                )
            if spot >= up.trigger:
                intents.append(
                    Intent(
                        IntentKind.ADD,
                        "extend up",
                        {
                            "strike": up.add_strike,
                            "wings": wings,
                        },
                    )
                )

        return intents

    def view_model(self, spot: float | None = None) -> dict[str, Any]:
        last = spot if spot is not None else self.spot
        if last is None:
            last = self.active_strikes()[-1] if self.active_strikes() else 0.0
        clock = self.flies[-1].opened_at if self.flies else time(10, 0)
        wall = book_clock(clock)
        up, down = self.watches(last, wall)
        wings = wings_for_clock(wall)
        used = self.flies[:BLOTTER_MAX]
        if not used:
            rows = empty_blotter(1)
        else:
            rows = [
                FlyRow(
                    slot=i,
                    strike=fly.strike,
                    status="closed" if fly.status == "closed" else "active",
                    put_wing=fly.wings,
                    call_wing=fly.wings,
                    sto=fly.credit,
                    btc_actual=fly.close_debit,
                    btc_est=None,
                    net=fly.net,
                    max_loss=max_loss_per_share(fly.wings, fly.credit),
                    origin=fly.origin,
                )
                for i, fly in enumerate(used, start=1)
            ]
        collected = sum(f.credit for f in self.flies)
        live_credit = sum(f.credit for f in self.active())
        closed_n = sum(1 for f in self.flies if f.status == "closed")
        used_close_pct = round(100 * closed_n / len(self.flies), 1) if self.flies else 0
        log = CaptainsLog(
            last_price=last,
            watch_spot=up.trigger,
            watch_add=up.add_strike,
            watch_var=up.trigger - last,
            next_spot=down.trigger,
            next_add=down.add_strike,
            next_var=down.trigger - last,
            atm_strike=round_to_5(last),
            wings=wings,
            max_opp=round(collected * 100, 0),
            remaining_opp=round(live_credit * 100, 0),
            used_close_pct=used_close_pct,
            btc_remaining_pct=round(100 - used_close_pct, 1) if self.flies else 0,
            auto_refresh=False,
            flies=rows,
        )
        data = log.as_dict()
        data["sample"] = False
        data["active"] = self.active_strikes()
        data["ladder"] = self.log_ladder(last, clock)
        data["ladder_alerts"] = add_watch_alerts(data["ladder"], last)
        data.update(self._max_profit())
        data["first_credit"] = self._first_credit
        data["regime"] = self.regime
        data["stop_pad"] = self.stop_pad
        data["scale"] = self.mode_at(clock).name
        return data

    def _max_profit(self) -> dict[str, object]:
        return max_profit_view(
            [(fly.strike, fly.credit, float(fly.wings), float(fly.wings)) for fly in self.active()],
            [fly.net for fly in self.flies if fly.status == "closed"],
        )

    def live_strip(self, spot: float, clock: time | datetime) -> dict[str, Any]:
        """Captain's Log strip: live ATM, Watch/Next from the open book when one exists."""
        wall = book_clock(clock)
        if not self.active():
            data = dict(next_fly_strip(spot, wall))
            data.update(self._max_profit())
            data["first_credit"] = self._first_credit
            data["regime"] = self.regime
            data["stop_pad"] = self.stop_pad
            data["scale"] = self.mode_at(wall).name
            # Stopped-out flies stay on the blotter. An empty book does not.
            if self.flies:
                data["flies"] = self.view_model(spot)["flies"]
        else:
            data = self.view_model(spot)
            data["last_price"] = spot
            atm = round_to_5(spot)
            data["atm_strike"] = atm
            data["wings"] = wings_for_clock(wall)
        data["ladder"] = self.log_ladder(spot, clock)
        data["ladder_alerts"] = add_watch_alerts(data["ladder"], spot)
        data["regime"] = self.regime
        data["stop_pad"] = self.stop_pad
        data["scale"] = self.mode_at(clock).name
        return data

    def _active(self, strike: float) -> OpenFly:
        for fly in self.active():
            if fly.strike == strike:
                return fly
        raise KeyError(f"no active fly at {strike}")
