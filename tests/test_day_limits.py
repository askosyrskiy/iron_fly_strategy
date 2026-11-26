import pytest

from iron_fly_strategy.spec import day_limit_points, day_pnl_breached, parse_spec


def test_day_limits_parse_and_convert_to_points():
    spec = parse_spec("wings: 20\nregime: s3\nday_loss_stop: -5000\nday_profit_goal: 2500\n")
    assert spec.day_loss_stop == -5000
    assert spec.day_profit_goal == 2500
    loss, goal = day_limit_points(spec)
    assert loss == -50.0
    assert goal == 25.0


def test_day_pnl_breached_checks_loss_before_goal():
    assert day_pnl_breached(-50.0, -50.0, 25.0) == "day_loss_stop"
    assert day_pnl_breached(-49.99, -50.0, 25.0) is None
    assert day_pnl_breached(25.0, None, 25.0) == "day_profit_goal"
    assert day_pnl_breached(24.99, None, 25.0) is None


def test_day_loss_stop_rejects_positive_and_profit_rejects_negative():
    with pytest.raises(ValueError, match="day_loss_stop"):
        parse_spec("day_loss_stop: 100\n")
    with pytest.raises(ValueError, match="day_profit_goal"):
        parse_spec("day_profit_goal: -1\n")