import pytest

from nito.config import LIVE_CONFIRM_PHRASE, ConfigError, Secrets, config_from_dict
from nito.models import Side, Trade, TradeStatus
from nito.simulation import step

from .conftest import T0


def _long():
    return Trade("X", Side.LONG, 1.0, 100.0, 99.0, 102.0, 101.0, T0)


def test_stop_wins_when_both_hit():
    t = _long()
    assert step(t, 100, 103, 98, T0, 0.0) == ["stop"]
    assert t.exit_price == 99.0 and t.exit_reason == "sl"
    assert t.pnl == pytest.approx(-1.0)


def test_gap_through_stop_exits_at_open():
    t = _long()
    step(t, 98.5, 98.8, 98.0, T0, 0.0)
    assert t.exit_price == 98.5


def test_target_and_fees():
    t = _long()
    assert step(t, 100.5, 102.5, 100.2, T0, 0.1) == ["target"]
    assert t.pnl == pytest.approx(2.0 - (100 + 102) * 0.001)
    assert t.r_multiple == pytest.approx(t.pnl / 1.0)


def test_break_even_then_exit_at_be():
    t = _long()
    assert step(t, 100.5, 101.2, 100.3, T0, 0.06) == ["break_even"]
    assert t.moved_to_be and t.stop_loss == pytest.approx(100.12)
    assert step(t, 100.5, 100.6, 100.0, T0, 0.06) == ["stop"]
    assert t.exit_reason == "be"
    assert t.pnl == pytest.approx(0.0, abs=1e-3)


def test_short_mirror():
    t = Trade("X", Side.SHORT, 1.0, 100.0, 101.0, 98.0, None, T0)
    assert step(t, 99.5, 99.8, 97.9, T0, 0.0) == ["target"]
    assert t.pnl == pytest.approx(2.0)
    assert step(t, 99, 99, 99, T0, 0.0) == []  # già chiusa
    assert t.status is TradeStatus.CLOSED


def test_default_config_is_valid_and_safe():
    cfg = config_from_dict({})
    assert cfg.mode.value == "signals"
    assert cfg.execution.require_confirmation
    assert cfg.market.testnet


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("risk", "risk_per_trade_pct", 5.0),
        ("risk", "max_leverage", 50),
        ("risk", "max_daily_loss_pct", 25.0),
        ("risk", "reward_risk", 0.8),
        ("strategy", "enabled", ["inesistente"]),
        ("market", "candles", 100),
    ],
)
def test_hard_limits(section, key, value):
    with pytest.raises(ConfigError):
        config_from_dict({section: {key: value}})


def test_unknown_keys_rejected():
    with pytest.raises(ConfigError):
        config_from_dict({"risk": {"rischio": 1}})


def test_live_requires_explicit_opt_in():
    with pytest.raises(ConfigError) as err:
        config_from_dict({"general": {"mode": "live"}}, Secrets())
    assert LIVE_CONFIRM_PHRASE in str(err.value)
    ok = Secrets(api_key="k", api_secret="s", live_confirm=LIVE_CONFIRM_PHRASE)
    assert config_from_dict({"general": {"mode": "live"}}, ok).is_live
    with pytest.raises(ConfigError):
        config_from_dict({"general": {"mode": "live"}, "market": {"market_type": "spot"}}, ok)
