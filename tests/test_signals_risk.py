import pytest

from nito.config import RiskConfig
from nito.models import Setup, Side, Trade
from nito.risk import RiskGuard, position_size
from nito.signals import build_signal, reprice
from nito.storage import Store

from .conftest import T0, make_series

SETUP = [Setup("Test", Side.LONG, "")]


def _series(low=99.0, high=100.0, close=100.0):
    n = 8
    return make_series(
        [close] * n,
        low=[low] * n,
        high=[high] * n,
        e10=99,
        e60=98,
        e223=97,
        atr=0.5,
    )


def test_long_signal_levels():
    s = _series(low=99.0)
    sig, why = build_signal(s, 7, Side.LONG, SETUP, RiskConfig(), "SOL/USDT:USDT", "5m")
    assert why == ""
    assert sig.stop_loss == pytest.approx(98.95)  # minimo candela - 0.1 ATR
    assert sig.take_profit == pytest.approx(102.1)  # 2R
    assert sig.break_even_trigger == pytest.approx(101.05)  # +1R
    assert sig.reward_risk == pytest.approx(2.0)
    assert sig.net_reward_risk == pytest.approx((2.1 - 0.12) / (1.05 + 0.12))


def test_short_signal_levels():
    s = _series(high=101.0)
    sig, _ = build_signal(s, 7, Side.SHORT, SETUP, RiskConfig(), "X", "5m")
    assert sig.stop_loss == pytest.approx(101.05)
    assert sig.take_profit == pytest.approx(97.9)


def test_tight_stop_is_widened():
    s = _series(low=99.99)
    sig, _ = build_signal(s, 7, Side.LONG, SETUP, RiskConfig(min_sl_pct=0.5), "X", "5m")
    assert sig.stop_loss == pytest.approx(99.5)


def test_wide_stop_rejected():
    s = _series(low=96.0)
    sig, why = build_signal(s, 7, Side.LONG, SETUP, RiskConfig(), "X", "5m")
    assert sig is None and why.startswith("SL troppo largo")


def test_fees_reject_small_scalps():
    s = _series(low=99.99)
    sig, why = build_signal(s, 7, Side.LONG, SETUP, RiskConfig(min_sl_pct=0.2), "X", "5m")
    assert sig is None and "commissioni" in why


def test_reprice_keeps_stop_and_rr():
    s = _series(low=99.0)
    sig, _ = build_signal(s, 7, Side.LONG, SETUP, RiskConfig(), "X", "5m")
    moved = reprice(sig, 100.2)
    assert moved.stop_loss == sig.stop_loss
    assert moved.reward_risk == pytest.approx(sig.reward_risk)
    with pytest.raises(ValueError):
        reprice(sig, 98.0)


def test_position_size_risk_and_leverage_cap():
    qty = position_size(1000, 1.0, 100, 99, 0.06, 3)
    assert qty == pytest.approx(10 / 1.12)
    assert (100 - 99 + 0.12) * qty == pytest.approx(10)  # perdita allo stop = 1% con commissioni
    assert position_size(1000, 1.0, 100, 99.99, 0.0, 3) == pytest.approx(30)  # cap leva
    assert position_size(0, 1.0, 100, 99, 0.06, 3) == 0


def _trade(pnl):
    t = Trade("X", Side.LONG, 1, 100, 99, 102, None, T0)
    t.pnl = pnl
    return t


def test_guard_daily_loss_and_new_day():
    store = Store(":memory:")
    g = RiskGuard(RiskConfig(max_daily_loss_pct=3), store)
    assert g.update_equity(1000, T0) is None
    assert g.update_equity(980, T0 + 1000) is None
    msg = g.update_equity(969, T0 + 2000)
    assert msg and "perdita giornaliera" in msg
    assert g.block_reason("X", []) == g.daily_halt
    # il giorno dopo si riparte
    assert g.update_equity(969, T0 + 86_400_000) is None
    assert g.block_reason("X", []) is None


def test_guard_consecutive_losses_and_resume():
    store = Store(":memory:")
    g = RiskGuard(RiskConfig(max_consecutive_losses=2), store)
    assert g.on_trade_closed(_trade(-5)) is None
    assert g.on_trade_closed(_trade(3)) is None  # una vincita azzera
    assert g.on_trade_closed(_trade(-5)) is None
    assert g.on_trade_closed(_trade(-5))
    assert g.block_reason("X", []).startswith("kill switch")
    g.resume()
    assert g.block_reason("X", []) is None


def test_guard_positions_and_cooldown():
    store = Store(":memory:")
    g = RiskGuard(RiskConfig(max_open_positions=1, cooldown_minutes=30), store)
    open_ = [Trade("A", Side.LONG, 1, 100, 99, 102, None, T0)]
    assert "già aperta" in g.block_reason("A", open_)
    assert "massimo" in g.block_reason("B", open_)
    g.mark_signal("B", T0)
    assert g.in_cooldown("B", T0 + 29 * 60_000)
    assert not g.in_cooldown("B", T0 + 31 * 60_000)


def test_state_is_separated_by_mode():
    paper = Store(":memory:", mode="paper")
    paper.set_state("kill_switch", "x")
    paper.mode = "live"
    assert paper.get_state("kill_switch") == ""
