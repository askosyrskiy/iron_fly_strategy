"""Slippage is a non-negative config field. A later pick replaces the desk value."""

from datetime import time

import pytest

from iron_fly_strategy.config_state import slippage_now
from iron_fly_strategy.spec import parse_spec

SPEC = """\
slippage: 0.5
picks:
  - at_time: "15:44"
    stop: 2
    slippage: 1.1
  - at_time: "15:55"
    slippage: 0.7
"""


def test_clock_pick_replaces_slippage_while_it_is_on():
    spec = parse_spec(SPEC)
    assert spec.slippage == 0.5
    assert slippage_now(spec, wall=time(15, 0)) == 0.5
    assert slippage_now(spec, wall=time(15, 44)) == 1.1
    assert slippage_now(spec, wall=time(15, 55)) == 0.7


def test_view_model_keeps_flies_past_the_old_blotter_cap():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from iron_fly_strategy.book import FlyBook

    book = FlyBook()
    when = datetime(2026, 10, 5, 10, 40, tzinfo=ZoneInfo("America/New_York"))
    for offset in range(21):
        book.open_fly(7000 + offset * 5, 1.0, 20, when)
    rows = book.view_model(7100)["flies"]
    assert len(rows) == 21
    assert rows[-1]["slot"] == 21
    assert rows[-1]["strike"] == 7100


def test_fee_per_contract_defaults_to_zero_and_rejects_a_negative():
    assert parse_spec("wings: 20\n").fee_per_contract == 0.0
    assert parse_spec("fee_per_contract: 1\n").fee_per_contract == 1.0
    with pytest.raises(ValueError, match="fee_per_contract"):
        parse_spec("fee_per_contract: -1\n")


def test_missing_slippage_is_zero_and_negative_is_rejected():
    assert parse_spec("wings: 20\n").slippage == 0.0
    with pytest.raises(ValueError, match="slippage"):
        parse_spec("slippage: -0.1\n")
