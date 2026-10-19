"""Current EM $ picks the credit-table row and can widen on the next print."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from iron_fly_strategy.book import FlyBook
from iron_fly_strategy.config_state import apply_config_state
from iron_fly_strategy.spec import parse_spec

_ET = ZoneInfo("America/New_York")
_WALL = time(10, 40)

SPEC = """\
regime: "±15/20"
em_regime: true
credit_regime: false
cookies_below: 50
cookies_min_flies: 1
stop: 0
picks:
  - at_time: "15:00"
    stop: 1
"""


def _apply(
    spec_text: str,
    *,
    em_now: float | None,
    em_pct: float | None = 70,
    wall: time = _WALL,
    book: FlyBook | None = None,
    latch: str | None = None,
):
    spec = parse_spec(spec_text)
    return apply_config_state(
        spec,
        book or FlyBook(),
        spot=6000,
        em_pct=em_pct,
        wall=wall,
        wing_width=None,
        wings=spec.wings,
        em_now=em_now,
        regime_latch=latch,
    )


def test_em_regime_flag_parses_and_defaults_off():
    assert parse_spec("em_regime: true\n").auto["em_regime"] is True
    assert parse_spec("em_regime: false\n").auto["em_regime"] is False
    assert "em_regime" not in parse_spec("wings: 50\n").auto
    with pytest.raises(ValueError, match="em_regime"):
        parse_spec("em_regime: maybe\n")


def test_em_dollars_use_the_credit_table_and_ignore_the_desk_cap():
    assert _apply(SPEC, em_now=14.99).regime == "s1"
    assert _apply(SPEC, em_now=15).regime == "s2"
    assert _apply(SPEC, em_now=19).regime == "s2"
    assert _apply(SPEC, em_now=19.01).regime == "s3"
    assert _apply(SPEC, em_now=24).regime == "s3"
    assert _apply(SPEC, em_now=24.89).regime == "s3"
    assert _apply(SPEC, em_now=24.90).regime == "s4"
    assert _apply(SPEC, em_now=30, latch="s1").regime == "s4"


def test_a_later_print_can_widen_when_em_rises():
    book = FlyBook()
    high = _apply(SPEC, em_now=25, book=book)
    assert high.regime == "s4"
    assert high.regime_latch == ""
    low = _apply(SPEC, em_now=24, book=book, latch=high.regime_latch or None)
    assert low.regime == "s3"
    back = _apply(SPEC, em_now=25, book=book, latch="s3")
    assert back.regime == "s4"
    assert back.regime_latch == ""


def test_missing_em_keeps_the_desk_regime_until_a_reading_arrives():
    book = FlyBook()
    missing = _apply(SPEC, em_now=None, book=book)
    assert missing.regime == "s3"
    wide = _apply(SPEC, em_now=30, book=book, latch=missing.regime_latch)
    assert wide.regime == "s4"


def test_em_is_ignored_before_the_open_print():
    assert _apply(SPEC, em_now=30, wall=time(9, 30)).regime == "s3"


def test_cookies_holds_only_while_em_pct_is_under_the_cut():
    book = FlyBook()
    book.open_fly(6000, 22.0, 50, datetime(2026, 10, 16, 10, 40, tzinfo=_ET))
    tight = _apply(SPEC, em_now=25, em_pct=40, book=book)
    assert tight.regime == "cookies"
    assert tight.regime_latch == ""
    back = _apply(SPEC, em_now=25, em_pct=60, book=book, latch="cookies")
    assert back.regime == "s4"


def test_credit_still_fills_in_when_em_is_missing_and_can_lose_to_em():
    text = SPEC.replace("credit_regime: false", "credit_regime: true")
    book = FlyBook()
    book.open_fly(6000, 12.0, 50, datetime(2026, 10, 16, 10, 40, tzinfo=_ET))
    assert _apply(text, em_now=None, book=book).regime == "s1"
    assert _apply(text, em_now=30, book=book, latch="s1").regime == "s4"


def test_clock_stop_still_applies_and_a_regime_pick_only_tightens():
    stopped = _apply(SPEC, em_now=25, wall=time(15, 0))
    assert stopped.regime == "s4"
    assert stopped.stop == 1
    text = """\
regime: "±15/20"
em_regime: true
credit_regime: false
picks:
  - at_em_pct: 60
    regime: "±7.5/10"
"""
    pinned = _apply(text, em_now=25, em_pct=50)
    assert pinned.regime == "s1"
    assert pinned.regime_latch == ""
    assert _apply(text, em_now=25, em_pct=70, latch=pinned.regime_latch or None).regime == "s4"


def test_em_regime_off_ignores_the_reading():
    text = SPEC.replace("em_regime: true", "em_regime: false")
    state = _apply(text, em_now=30)
    assert state.regime == "s3"
    assert state.regime_latch == "s3"
