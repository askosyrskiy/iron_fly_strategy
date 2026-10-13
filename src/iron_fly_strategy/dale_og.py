"""Dale's — default SPX 0DTE iron fly (https://www.youtube.com/watch?v=kNR2r8Nj3qI).

Each tick: `on_quote(quote)` then `pending_intents()`. This file decides
when to add or close. Live auto and backtest both execute those intents.

Captain's Log display still reads `self.book` (ladder / strip). Change
open/close here; copy this file to fork a variation.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from iron_fly_strategy.book import FlyBook
from iron_fly_strategy.models import Intent, IntentKind, Quote, SessionMode
from iron_fly_strategy.rules import (
    book_clock,
    credit_drops,
    round_to_5,
    tagged_add_strike,
    wings_for_clock,
)

_ET = ZoneInfo("America/New_York")

# Auto first fly at this ET clock (inclusive). None = human Open click.
# Live/sim: Captain's Log F1 time + Auto open override this. Backtest: `--f1-at`.
F1_AT: time | None = None
MAX_OPEN = 5
# No ADD/CLOSE at or after this ET clock (16:00 cash print still trades).
TRADE_STOP = time(16, 1)
# No new fly at or after this ET clock. Drops still run until TRADE_STOP.
ADD_UNTIL = time(15, 57)


class DaleOg:
    id = "dale-og"
    display_name = "Dale's iron fly"
    underlyings = ("SPX",)
    mode = SessionMode.CONFIRM
    f1_at = F1_AT
    max_open = MAX_OPEN
    add_until = ADD_UNTIL

    def __init__(self, book: FlyBook | None = None) -> None:
        self.book = book or FlyBook()
        self._clock: datetime | None = None
        self._prev_spot: float | None = None
        self._prev_mode: str | None = None

    def on_quote(self, quote: Quote) -> None:
        if quote.symbol not in self.underlyings:
            return
        if self.book.spot is not None:
            self._prev_spot = self.book.spot
            when = quote.ts or self._clock
            if when is not None:
                self._prev_mode = self.book.mode_at(when).name
        self.book.on_quote(quote)
        if quote.ts is not None:
            self._clock = quote.ts

    def apply_intent(self, intent: Intent, ts: datetime | None = None) -> None:
        when = ts or self._clock or datetime.now(_ET)
        strike = float(intent.payload["strike"])
        if intent.kind is IntentKind.ADD:
            wings = int(intent.payload.get("wings", 50))
            credit = float(intent.payload.get("credit", 0))
            self.book.open_fly(
                strike,
                credit,
                wings,
                when,
                origin=str(intent.payload.get("origin") or ""),
            )
        elif intent.kind is IntentKind.CLOSE:
            debit = float(intent.payload.get("debit", 0))
            self.book.close_fly(strike, debit, when)
        elif intent.kind is IntentKind.FLATTEN:
            for fly in list(self.book.active()):
                self.book.close_fly(fly.strike, fly.credit, when)
        else:
            raise ValueError(intent.kind)

    def pending_intents(
        self,
        now: datetime | None = None,
        *,
        wings: int | None = None,
        chain=None,
    ) -> list[Intent]:
        del chain
        if self.book.spot is None:
            return []
        clock = now or self._clock or datetime.now(_ET)
        return self.decide(self.book.spot, clock, wings=wings)

    def decide(self, spot: float, ts: datetime, *, wings: int | None = None) -> list[Intent]:
        """Open/close policy. Every breached fly closes on this print. One ADD otherwise."""
        wall = book_clock(ts)
        if wall >= TRADE_STOP:
            return []
        shorts = self.book.active_strikes()
        width = wings if wings is not None else wings_for_clock(wall)
        flatten_at = getattr(self, "flatten_at", None)
        if flatten_at is not None and wall >= flatten_at:
            if not shorts:
                return []
            strike = min(shorts, key=lambda item: abs(item - spot))
            return [
                Intent(
                    IntentKind.CLOSE,
                    "flatten_at",
                    {"strike": strike},
                )
            ]

        if not shorts:
            if not self._opens_allowed(wall):
                return []
            if self.f1_at is None:
                # Manual first fly (or F1 held for EM). Closed blotter flies do not count.
                return []
            if self._cookie_armed() and not self.book.cookie_opened:
                return [self._cookie_intent(spot, width)]
            # Past F1 with nothing active: always the first ATM. Closed flies never
            # unlock watch-tagged adds before that first open.
            return [
                Intent(
                    IntentKind.ADD,
                    "first ATM fly",
                    {"strike": round_to_5(spot), "wings": width},
                )
            ]

        drops = credit_drops(
            [(fly.strike, fly.credit) for fly in self.book.active()],
            spot,
            float(self.book.stop_pad),
        )
        if drops:
            return [
                Intent(
                    IntentKind.CLOSE,
                    "expiration breakeven",
                    {"strike": strike},
                )
                for strike in drops
            ]

        if not self._opens_allowed(wall):
            return []

        cookie = self._cookie_open(spot, width)
        if cookie is not None:
            return [cookie]

        if len(shorts) >= self.max_open:
            return []

        add = self._crossed_add(spot, ts, width)
        return [add] if add is not None else []

    def _opens_allowed(self, wall: time) -> bool:
        """Adds only after F1 (when set) and before add_until. Drops ignore this."""
        if self.f1_at is not None and wall < self.f1_at:
            return False
        return self._adds_allowed(wall)

    def _adds_allowed(self, wall: time) -> bool:
        until = self.add_until
        if until is None:
            return True
        if isinstance(until, str):
            until = time.fromisoformat(until)
        return wall < until

    def _cookie_armed(self) -> bool:
        if not getattr(self, "arm_cookie", True):
            return False
        return self.book.regime == "cookies"

    def _cookie_open(self, spot: float, width: int) -> Intent | None:
        """One fly at the 5-point around last when Cookies arms. Then Watch/Next."""
        if not self._cookie_armed() or self.book.cookie_opened:
            return None
        strike = round_to_5(spot)
        if strike in self.book.active_strikes():
            self.book.mark_cookie(strike)
            return None
        if len(self.book.active()) >= self.max_open:
            return None
        return self._cookie_intent(spot, width)

    def _cookie_intent(self, spot: float, width: int) -> Intent:
        return Intent(
            IntentKind.ADD,
            "cookie ATM",
            {"strike": round_to_5(spot), "wings": width, "origin": "cookie"},
        )

    def _crossed_add(self, spot: float, ts: datetime, width: int) -> Intent | None:
        """Watch / Next add last newly crossed after this grid was armed."""
        target = tagged_add_strike(
            self.book.log_ladder(spot, ts),
            spot=spot,
            shorts=set(self.book.active_strikes()),
            prev_spot=self._prev_spot,
            prev_mode=self._prev_mode,
            mode=self.book.mode_at(ts).name,
        )
        if target is None:
            return None
        return Intent(
            IntentKind.ADD,
            "watch tagged",
            {"strike": target, "wings": width},
        )

    def tick_line(self, now: datetime | None = None, *, wings: int | None = None) -> dict[str, Any]:
        """One log row for this tick: clock, SPX, open shorts, and what the book is doing."""
        clock = now or self._clock or datetime.now(_ET)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=_ET)
        else:
            clock = clock.astimezone(_ET)
        spot = self.book.spot
        strikes = [int(round(strike)) for strike in self.book.active_strikes()]
        intents = self.pending_intents(clock, wings=wings) if spot is not None else []
        return {
            "ts": clock,
            "spot": spot,
            "strikes": strikes,
            "message": self._tick_message(spot, clock, intents),
        }

    def _tick_message(self, spot: float | None, clock: datetime, intents: list[Intent]) -> str:
        wall = book_clock(clock)
        shorts = self.book.active_strikes()
        if spot is None:
            return "no SPX quote"
        if wall >= TRADE_STOP:
            return "session closed · 16:01 ET"
        if intents:
            intent = intents[0]
            raw = intent.payload.get("strike")
            k = str(int(round(float(raw)))) if raw is not None else "?"
            if intent.kind is IntentKind.CLOSE:
                return f"closing fly {k} ({intent.reason})"
            if intent.kind is IntentKind.ADD:
                if intent.reason == "first ATM fly":
                    return f"opening fly {k} (first ATM)"
                if intent.reason == "cookie ATM":
                    return f"opening fly {k} (cookie ATM)"
                return f"opening fly {k} ({intent.reason})"
            return f"{intent.kind.value} {k} ({intent.reason})"
        if not shorts:
            if self.f1_at is None:
                block = getattr(self, "f1_block", None)
                if block:
                    return str(block)
                return "waiting for Open click"
            if wall < self.f1_at:
                return f"waiting for F1 at {self.f1_at.strftime('%H:%M')} ET"
            return "F1 due"
        if len(shorts) >= self.max_open:
            return f"at max {self.max_open}"
        nearest: tuple[float, str, float | None, float, str] | None = None
        for row in self.book.log_ladder(spot, clock):
            if not row.get("enabled"):
                continue
            watch = row.get("watch")
            if watch is None:
                continue
            dist = abs(spot - float(watch))
            kind = str(row.get("kind") or "add")
            strike = row.get("strike")
            side = str(row.get("side") or "")
            target = float(strike) if strike is not None else None
            if nearest is None or dist < nearest[0]:
                nearest = (dist, kind, target, float(watch), side)
        if nearest is not None:
            dist, kind, strike, watch, side = nearest
            label = "drop" if kind == "drop" else "add"
            k = str(int(round(strike))) if strike is not None else "?"
            side_txt = f" {side}" if side else ""
            return f"watch near {label} {k} at {watch:g} ({dist:.1f} pts{side_txt})"
        return "watching"

    def view_model(self, spot: float | None = None) -> dict[str, Any]:
        return self.book.view_model(spot)
