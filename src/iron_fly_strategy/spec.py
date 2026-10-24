"""One-day desk YAML. Use Config always runs automatic trading from these rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import yaml

from iron_fly_strategy.rules import REGIME_LABELS

WING_CHOICES = ("auto", "50", "40", "30", "25", "20", "15", "10")
DEFAULT_SPEC = """\
f1_at: "10:40"              # open the first fly at this clock
f1_below: 80                # AND below this EM%, open the first fly
wings: 50                   # starting width; later steps can only narrow it
regime: "±2.5/5"            # watch ±15, add at 20; credit regime cannot go wider
stop: 0                     # drop at the credit breakeven (K ± credit)
credit_regime: true         # after a 50-wide fill, the grid follows that credit
cookies_below: 50           # under this EM%, switch to cookies and stay there
cookies_min_flies: 1        # need this many open flies to enter cookies
follow_wings: true          # from 9:35, width tracks the 0.40 put and never widens
wing_steps: [50, 30, 20]    # widths follow_wings may snap to
add_until: "15:58"          # no new fly from here; drops still run until 16:01
flatten_at: "15:49"         # flatten the wings at 15:00
leftover_until: "15:45"     # stop sell/roll of parked wings; reopen can reuse them
roll_above: 0.50            # roll a parked long when its value is above this
roll_to: 0.40               # roll it to the strike nearest this price
#fill_mode_open: mid        # mid or bid (worst STO credit)
#fill_mode_close: mid       # mid or ask (worst BTC debit)
picks:                      # a clock stays on; EM and EM% follow the reading
  - at_time: "15:00"        # from 15:00
    stop: 1
  - at_em_pct: "65"         # EM% below 70
    regime: "±7.5/10"       # change regime
"""
# The classroom file before the EM picks. A saved copy of this still upgrades.
PRIOR_CLASSROOM_PICKS = (({"at": "15:00", "stop": 1},),)
FILL_MODE_OPEN = ("mid", "bid")
FILL_MODE_CLOSE = ("mid", "ask")
FILL_MODE_DEFAULT = "mid"
_REGIME = {
    "s1": "s1",
    "s2": "s2",
    "s3": "s3",
    "s4": "s4",
    "ladder": "ladder",
    "cookies": "cookies",
    "+-7.5/10": "s1",
    "7.5/10": "s1",
    "+-7/10": "s1",
    "7/10": "s1",
    "+-10/15": "s2",
    "10/15": "s2",
    "+-15/20": "s3",
    "15/20": "s3",
    "+-20/25": "s4",
    "20/25": "s4",
    "+-2.5/5": "ladder",
}
_AUTO = {
    "f1_below",
    "credit_regime",
    "em_regime",
    "cookies_below",
    "cookies_min_flies",
    "follow_wings",
    "wing_steps",
    "add_until",
    "flatten_at",
    "leftover_until",
    "roll_above",
    "roll_to",
    "regime_max",
}
# Nested `auto:` still accepted for older saved YAML.
_TOP = {
    "f1_at",
    "wings",
    "regime",
    "stop",
    "desk",
    "auto",
    "picks",
    "strategy",
    "date",
    "close_fill_ms",
    "fill_mode_open",
    "fill_mode_close",
    "slippage",
    "fee_per_contract",
    *_AUTO,
}
CLOSE_FILL_MS_DEFAULT = 250
CLOSE_FILL_MS_MAX = 5000
LEFTOVER_UNTIL_DEFAULT = time(15, 45)
ROLL_ABOVE_DEFAULT = 0.50
ROLL_TO_DEFAULT = 0.40


@dataclass(frozen=True)
class DeskSpec:
    yaml: str
    f1_at: time | None
    wings: str
    regime: str
    stop: int
    desk: str
    auto: dict[str, object]
    picks: tuple[dict[str, object], ...]
    close_fill_ms: int = CLOSE_FILL_MS_DEFAULT
    fill_mode_open: str = FILL_MODE_DEFAULT
    fill_mode_close: str = FILL_MODE_DEFAULT
    slippage: float = 0.0
    fee_per_contract: float = 0.0

    def summary(self) -> str:
        f1 = "click" if self.f1_at is None else self.f1_at.strftime("%H:%M")
        picks = f"{len(self.picks)} pick" if len(self.picks) == 1 else f"{len(self.picks)} picks"
        line = (
            f"Desk {self.desk} · F1 {f1} · wings {self.wings} · "
            f"{REGIME_LABELS[self.regime]} · stop {self.stop} · {picks}"
        )
        if self.fill_mode_open != FILL_MODE_DEFAULT or self.fill_mode_close != FILL_MODE_DEFAULT:
            line += f" · fill {self.fill_mode_open}/{self.fill_mode_close}"
        return line


def parse_spec(text: str) -> DeskSpec:
    if len(text or "") > 32_000:
        raise ValueError("configuration is too long")
    try:
        raw = yaml.safe_load(text or "")
    except yaml.YAMLError as exc:
        raise ValueError("configuration is not valid YAML") from exc
    if raw is None:
        raise ValueError("configuration is empty")
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a YAML mapping")
    extra = sorted(set(raw) - _TOP)
    if extra:
        raise ValueError(f"unknown field {extra[0]}")
    strategy = str(raw.get("strategy") or "").strip().lower()
    if strategy and strategy not in ("dale-og", "dale-og-auto"):
        raise ValueError(f"strategy must be dale-og, got {raw.get('strategy')!r}")
    auto = _auto(_merge_auto(raw))
    # Use Config always means automatic trading; desk: is legacy (default auto).
    desk = _desk(raw.get("desk"), auto_present=True)
    picks = _picks(raw.get("picks"))
    return DeskSpec(
        yaml=text,
        f1_at=None if raw.get("f1_at") in (None, "") else _clock(raw.get("f1_at"), "f1_at"),
        wings=_wings(raw.get("wings", "auto")),
        regime=_regime(raw.get("regime", "s1")),
        stop=_stop(raw.get("stop", 0)),
        desk=desk,
        auto=auto,
        picks=tuple(picks),
        close_fill_ms=_close_fill_ms(raw.get("close_fill_ms", CLOSE_FILL_MS_DEFAULT)),
        fill_mode_open=_fill_mode(raw.get("fill_mode_open"), FILL_MODE_OPEN, "fill_mode_open"),
        fill_mode_close=_fill_mode(raw.get("fill_mode_close"), FILL_MODE_CLOSE, "fill_mode_close"),
        slippage=_slippage(raw.get("slippage", 0)),
        fee_per_contract=_fee_per_contract(raw.get("fee_per_contract", 0)),
    )


def _merge_auto(raw: dict) -> dict:
    """Flat top-level auto fields win over a nested `auto:` block."""
    nested = raw.get("auto")
    base: dict = dict(nested) if isinstance(nested, dict) else {}
    for key in _AUTO:
        if key in raw:
            base[key] = raw[key]
    return base


def _close_fill_ms(raw: object) -> int:
    if raw in (None, ""):
        return CLOSE_FILL_MS_DEFAULT
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"close_fill_ms must be an integer 0–{CLOSE_FILL_MS_MAX}, got {raw!r}") from exc
    if value < 0 or value > CLOSE_FILL_MS_MAX:
        raise ValueError(f"close_fill_ms must be an integer 0–{CLOSE_FILL_MS_MAX}, got {raw!r}")
    return value


def _fill_mode(raw: object, allowed: tuple[str, ...], field: str) -> str:
    if raw in (None, ""):
        return FILL_MODE_DEFAULT
    text = str(raw).strip().lower()
    if text not in allowed:
        choices = " or ".join(allowed)
        raise ValueError(f"{field} must be {choices}, got {raw!r}")
    return text


def _desk(raw: object, *, auto_present: bool) -> str:
    if raw in (None, ""):
        return "auto" if auto_present else "manual"
    name = str(raw).strip().lower()
    if name in ("picks", "click", "clicks"):
        return "manual"
    if name not in ("manual", "auto"):
        raise ValueError(f"desk must be manual or auto, got {raw!r}")
    return name


def _wings(raw: object) -> str:
    choice = str(raw if raw is not None else "auto").strip().lower()
    if choice not in WING_CHOICES:
        raise ValueError(f"wings must be one of {', '.join(WING_CHOICES)}")
    return choice


def _regime(raw: object) -> str:
    text = str(raw or "").strip().lower().replace("±", "+-").replace(" ", "")
    if text not in _REGIME:
        raise ValueError(f"unknown regime {raw!r}")
    return _REGIME[text]


def _stop(raw: object) -> int:
    from iron_fly_strategy.rules import STOP_PAD_MAX, valid_stop_pad

    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"stop must be an integer 0–{STOP_PAD_MAX}, got {raw!r}") from exc
    if not valid_stop_pad(value):
        raise ValueError(f"stop must be an integer 0–{STOP_PAD_MAX}, got {raw!r}")
    return value


def _clock(raw: object, field: str) -> time:
    text = str(raw).strip().strip('"').strip("'")
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"{field} must be HH:MM, got {raw!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        return time(hour, minute, second)
    except ValueError as exc:
        raise ValueError(f"{field} must be HH:MM, got {raw!r}") from exc


def _auto(raw: object) -> dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("auto must be a mapping")
    extra = sorted(set(raw) - _AUTO)
    if extra:
        raise ValueError(f"unknown auto field {extra[0]}")
    out: dict[str, object] = {}
    if "f1_below" in raw:
        out["f1_below"] = _number(raw["f1_below"], "f1_below")
    if "cookies_below" in raw:
        out["cookies_below"] = _number(raw["cookies_below"], "cookies_below")
    if "cookies_min_flies" in raw:
        out["cookies_min_flies"] = _count(raw["cookies_min_flies"])
    if "credit_regime" in raw:
        out["credit_regime"] = _flag(raw["credit_regime"], "credit_regime")
    if "em_regime" in raw:
        out["em_regime"] = _flag(raw["em_regime"], "em_regime")
    if "follow_wings" in raw:
        out["follow_wings"] = _flag(raw["follow_wings"], "follow_wings")
    if "wing_steps" in raw:
        out["wing_steps"] = _steps(raw["wing_steps"])
    if "add_until" in raw and raw["add_until"] not in (None, ""):
        out["add_until"] = _clock(raw["add_until"], "add_until").strftime("%H:%M")
    if "flatten_at" in raw and raw["flatten_at"] not in (None, ""):
        out["flatten_at"] = _clock(raw["flatten_at"], "flatten_at").strftime("%H:%M")
    if "leftover_until" in raw and raw["leftover_until"] not in (None, ""):
        out["leftover_until"] = _clock(raw["leftover_until"], "leftover_until").strftime("%H:%M")
    if "roll_above" in raw and raw["roll_above"] not in (None, ""):
        out["roll_above"] = _price(raw["roll_above"], "roll_above")
    if "roll_to" in raw and raw["roll_to"] not in (None, ""):
        out["roll_to"] = _price(raw["roll_to"], "roll_to")
    if "regime_max" in raw and raw["regime_max"] not in (None, ""):
        out["regime_max"] = _regime(raw["regime_max"])
    return out


def roll_above(auto: dict[str, object] | None) -> float:
    """Roll a parked long when its value is above this. Default 0.50."""
    return _stored_price(auto, "roll_above", ROLL_ABOVE_DEFAULT)


def roll_to(auto: dict[str, object] | None) -> float:
    """Roll a parked long to the strike nearest this price. Default 0.40."""
    return _stored_price(auto, "roll_to", ROLL_TO_DEFAULT)


def _stored_price(auto: dict[str, object] | None, key: str, default: float) -> float:
    raw = (auto or {}).get(key)
    if raw in (None, ""):
        return default
    return float(raw)


def leftover_until_clock(auto: dict[str, object] | None) -> time:
    """Clock when parked-wing sell/roll stops. Default 15:45."""
    raw = (auto or {}).get("leftover_until")
    if raw in (None, ""):
        return LEFTOVER_UNTIL_DEFAULT
    return time.fromisoformat(str(raw))


_PICK_FIELDS = {"at", "at_time", "at_em_pct", "at_em", "wings", "regime", "stop", "slippage"}
_PICK_WHEN = ("at", "at_time", "at_em_pct", "at_em")


def _picks(raw: object) -> list[dict[str, object]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("picks must be a list")
    rows: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each pick must be a mapping")
        extra = sorted(set(item) - _PICK_FIELDS)
        if extra:
            raise ValueError(f"unknown pick field {extra[0]}")
        triggers = [key for key in _PICK_WHEN if item.get(key) not in (None, "")]
        if len(triggers) != 1:
            raise ValueError("each pick needs one of at, at_time, at_em_pct, or at_em")
        trigger = triggers[0]
        row: dict[str, object] = {}
        if trigger in ("at", "at_time"):
            row["at"] = _clock(item[trigger], trigger).strftime("%H:%M")
        elif trigger == "at_em_pct":
            row["at_em_pct"] = _number(item[trigger], "at_em_pct")
        else:
            row["at_em"] = _number(item[trigger], "at_em")
        if item.get("wings") not in (None, ""):
            row["wings"] = _wings(item["wings"])
        if item.get("regime") not in (None, ""):
            row["regime"] = _regime(item["regime"])
        if item.get("stop") not in (None, ""):
            row["stop"] = _stop(item["stop"])
        if item.get("slippage") not in (None, ""):
            row["slippage"] = _slippage(item["slippage"])
        rows.append(row)
    return rows


def _fee_per_contract(raw: object) -> float:
    if raw in (None, ""):
        return 0.0
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"fee_per_contract must be a number 0 or more, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"fee_per_contract must be a number 0 or more, got {raw!r}")
    return value


def _slippage(raw: object) -> float:
    if raw in (None, ""):
        return 0.0
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"slippage must be a number 0 or more, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"slippage must be a number 0 or more, got {raw!r}")
    return value


def _price(raw: object, field: str) -> float:
    value = _number(raw, field)
    if value < 0:
        raise ValueError(f"{field} must be 0 or more")
    return value


def _number(raw: object, field: str) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc


def _count(raw: object) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("cookies_min_flies must be a whole number") from exc
    if value < 0:
        raise ValueError("cookies_min_flies must be a whole number")
    return value


def _flag(raw: object, field: str) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{field} must be true or false")


def _steps(raw: object) -> list[int]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("wing_steps must be a list of widths")
    steps: list[int] = []
    for item in raw:
        choice = _wings(item)
        if choice == "auto":
            raise ValueError("wing_steps cannot include auto")
        steps.append(int(choice))
    return steps
