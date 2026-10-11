import pytest

from nito.brokers import BrokerError, LiveBroker
from nito.config import LIVE_CONFIRM_PHRASE, Secrets, config_from_dict
from nito.models import Setup, Side, Signal, TradeStatus

from .conftest import T0, no_sleep

SYM = "SOL/USDT:USDT"


@pytest.fixture
def live_cfg():
    sec = Secrets(api_key="k", api_secret="s", live_confirm=LIVE_CONFIRM_PHRASE)
    return config_from_dict(
        {"general": {"mode": "live"}, "market": {"symbols": [SYM]}}, sec
    )


def _signal():
    return Signal(
        symbol=SYM,
        timeframe="5m",
        side=Side.LONG,
        setups=[Setup("Viagra", Side.LONG, "")],
        entry=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        break_even_trigger=101.0,
        candle_ts=T0,
        trend_up=True,
        fee_pct=0.06,
        id=1,
    )


def _orders(ex, key):
    return [c for c in ex.calls if c[0] == "order" and key in c[3]]


async def test_open_places_stop_and_target(fake_exchange, live_cfg):
    broker = LiveBroker(fake_exchange, live_cfg, sleep=no_sleep)
    trade = await broker.open_trade(_signal(), 5.0, T0)
    assert trade.qty == 5.0 and trade.entry == 100.0
    stop = _orders(fake_exchange, "stopLossPrice")
    tp = _orders(fake_exchange, "takeProfitPrice")
    assert stop and stop[0][3] == {"stopLossPrice": 99.0, "reduceOnly": True}
    assert tp and tp[0][3]["takeProfitPrice"] == 102.0
    assert ("leverage", 3, SYM) in fake_exchange.calls
    assert trade.sl_order_id and trade.tp_order_id


async def test_tp_recomputed_from_fill(fake_exchange, live_cfg):
    fake_exchange.fill_price = 100.5
    trade = await LiveBroker(fake_exchange, live_cfg, sleep=no_sleep).open_trade(_signal(), 5.0, T0)
    assert trade.stop_loss == 99.0
    assert trade.take_profit == pytest.approx(100.5 + 1.5 * 2)  # stesso R:R dal prezzo reale


async def test_stop_rejected_closes_position(fake_exchange, live_cfg):
    fake_exchange.fail_stop = 5
    broker = LiveBroker(fake_exchange, live_cfg, sleep=no_sleep)
    with pytest.raises(BrokerError, match="posizione chiusa"):
        await broker.open_trade(_signal(), 5.0, T0)
    assert SYM not in fake_exchange.positions  # nessuna posizione senza stop
    closes = [c for c in fake_exchange.calls if c[0] == "order" and c[3] == {"reduceOnly": True}]
    assert closes and closes[0][1] == "sell"


async def test_stop_rejected_and_close_fails_is_loud(fake_exchange, live_cfg):
    fake_exchange.fail_stop = 5
    fake_exchange.fail_close = True
    with pytest.raises(BrokerError, match="INTERVIENI MANUALMENTE"):
        await LiveBroker(fake_exchange, live_cfg, sleep=no_sleep).open_trade(_signal(), 5.0, T0)


async def test_liquidation_before_stop_aborts(fake_exchange, live_cfg):
    fake_exchange.liquidation = 99.5  # verrebbe liquidato prima dello stop a 99
    with pytest.raises(BrokerError, match="liquidazione"):
        await LiveBroker(fake_exchange, live_cfg, sleep=no_sleep).open_trade(_signal(), 5.0, T0)
    assert SYM not in fake_exchange.positions


async def test_quantity_below_minimum(fake_exchange, live_cfg):
    with pytest.raises(BrokerError, match="minimo"):
        await LiveBroker(fake_exchange, live_cfg, sleep=no_sleep).open_trade(_signal(), 0.05, T0)
    assert not [c for c in fake_exchange.calls if c[0] == "order"]


async def test_refuses_existing_position(fake_exchange, live_cfg):
    fake_exchange.positions[SYM] = {"symbol": SYM, "contracts": 1}
    with pytest.raises(BrokerError, match="già una posizione"):
        await LiveBroker(fake_exchange, live_cfg, sleep=no_sleep).open_trade(_signal(), 5.0, T0)


async def test_break_even_places_new_stop_before_cancel(fake_exchange, live_cfg):
    broker = LiveBroker(fake_exchange, live_cfg, sleep=no_sleep)
    trade = await broker.open_trade(_signal(), 5.0, T0)
    old_sl = trade.sl_order_id
    fake_exchange.price = 101.2
    fake_exchange.calls.clear()
    events = await broker.refresh(trade, T0 + 1)
    assert [e.kind for e in events] == ["break_even"]
    kinds = [c[0] for c in fake_exchange.calls]
    assert kinds.index("order") < kinds.index("cancel")  # prima il nuovo stop, poi la cancellazione
    assert ("cancel", old_sl) in fake_exchange.calls
    assert trade.moved_to_be and trade.stop_loss == pytest.approx(100.12)


async def test_refresh_detects_exchange_close(fake_exchange, live_cfg):
    broker = LiveBroker(fake_exchange, live_cfg, sleep=no_sleep)
    trade = await broker.open_trade(_signal(), 5.0, T0)
    fake_exchange.positions.pop(SYM)  # lo stop è scattato sull'exchange
    fake_exchange.price = 99.0
    events = await broker.refresh(trade, T0 + 1)
    assert [e.kind for e in events] == ["closed"]
    assert trade.status is TradeStatus.CLOSED and trade.exit_reason == "sl"
    assert trade.r_multiple == pytest.approx(-1.0, abs=0.15)


async def test_software_stop_when_exchange_stop_missing(fake_exchange, live_cfg):
    broker = LiveBroker(fake_exchange, live_cfg, sleep=no_sleep)
    trade = await broker.open_trade(_signal(), 5.0, T0)
    fake_exchange.price = 98.7  # 0.3R oltre lo stop, ma la posizione è ancora aperta
    events = await broker.refresh(trade, T0 + 1)
    assert events[0].kind == "closed" and trade.exit_reason == "sl_software"
    assert SYM not in fake_exchange.positions


async def test_software_take_profit_when_tp_rejected(fake_exchange, live_cfg):
    fake_exchange.fail_tp = True
    broker = LiveBroker(fake_exchange, live_cfg, sleep=no_sleep)
    trade = await broker.open_trade(_signal(), 5.0, T0)
    assert trade.tp_order_id is None
    fake_exchange.price = 102.1
    events = await broker.refresh(trade, T0 + 1)
    assert events[0].kind == "closed" and trade.exit_reason == "tp"


async def test_reconcile_warns_about_unmanaged_positions(fake_exchange, live_cfg):
    fake_exchange.positions[SYM] = {"symbol": SYM, "contracts": 2}
    warnings = await LiveBroker(fake_exchange, live_cfg, sleep=no_sleep).reconcile([])
    assert warnings and "non gestita" in warnings[0]
