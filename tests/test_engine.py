import pytest

from nito import engine as engine_mod
from nito.brokers import PaperBroker
from nito.config import config_from_dict
from nito.engine import Engine
from nito.models import Candle, DailyLevels, Setup, Side, Signal
from nito.storage import Store

from .conftest import T0, FakeNotifier

SYM = "SOL/USDT:USDT"


class FakeData:
    def __init__(self, price=100.0, candles=None):
        self.price = price
        self.candles = candles or []

    async def closed_candles(self, symbol, timeframe, limit):
        return self.candles[-limit:]

    async def daily_levels(self, symbol):
        return DailyLevels()

    async def last_price(self, symbol):
        return self.price


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _signal(entry=100.0):
    return Signal(
        symbol=SYM,
        timeframe="5m",
        side=Side.LONG,
        setups=[Setup("Viagra", Side.LONG, "5 chiusure")],
        entry=entry,
        stop_loss=99.0,
        take_profit=102.0,
        break_even_trigger=101.0,
        candle_ts=T0,
        trend_up=True,
        fee_pct=0.06,
    )


def _engine(mode="signals", confirm=True, **risk):
    cfg = config_from_dict(
        {
            "general": {"mode": mode},
            "market": {"symbols": [SYM], "btc_filter": False},
            "execution": {"require_confirmation": confirm},
            "risk": risk,
        }
    )
    store = Store(":memory:", mode=mode)
    data = FakeData()
    broker = PaperBroker(store, cfg.risk.account_equity, cfg.risk.fee_pct, data.last_price)
    notifier = FakeNotifier()
    clock = Clock()
    return Engine(cfg, data, broker, store, notifier, clock), data, notifier, clock


async def test_signals_mode_sends_and_tracks_outcome():
    eng, data, notifier, clock = _engine("signals")
    assert await eng.on_signal(_signal())
    text = notifier.messages[0][0]
    assert "LONG" in text and "TP" in text and "SL" in text and "102.0000" in text
    assert notifier.messages[0][1] is None  # nessun pulsante in modalità segnali
    assert len(eng.store.open_trades()) == 1

    data.price = 102.5
    await eng.monitor()
    assert "Take Profit raggiunto" in notifier.texts()
    assert eng.store.open_trades() == []
    assert await eng.broker.equity() > 1000


async def test_cooldown_and_one_position_per_symbol():
    eng, data, notifier, clock = _engine("signals")
    assert await eng.on_signal(_signal())
    clock.t += 60_000
    assert not await eng.on_signal(_signal())  # cooldown / posizione già aperta
    assert len(notifier.messages) == 1


async def test_paper_confirmation_flow():
    eng, data, notifier, clock = _engine("paper", confirm=True)
    await eng.on_signal(_signal())
    text, buttons = notifier.messages[0]
    assert "Confermi" in text
    sid = int(buttons[0][0][1].split(":")[1])
    assert eng.store.open_trades() == []

    data.price = 100.1
    result = await eng.confirm(sid)
    assert "eseguito" in result
    trades = eng.store.open_trades()
    assert len(trades) == 1 and trades[0].entry == 100.1  # entrata al prezzo attuale
    assert "già gestito" in await eng.confirm(sid)  # doppio click


async def test_confirmation_rejects_price_drift():
    eng, data, notifier, clock = _engine("paper", confirm=True)
    await eng.on_signal(_signal())
    sid = int(notifier.messages[0][1][0][0][1].split(":")[1])
    data.price = 100.5  # 0.5R > max_slippage_r 0.25
    assert "troppo lontano" in await eng.confirm(sid)
    assert eng.store.open_trades() == []


async def test_confirmation_expires():
    eng, data, notifier, clock = _engine("paper", confirm=True)
    await eng.on_signal(_signal())
    sid = int(notifier.messages[0][1][0][0][1].split(":")[1])
    clock.t += 10 * 60_000
    assert "scaduto" in await eng.confirm(sid)
    assert eng.store.open_trades() == []


async def test_skip():
    eng, data, notifier, clock = _engine("paper", confirm=True)
    await eng.on_signal(_signal())
    sid = int(notifier.messages[0][1][0][0][1].split(":")[1])
    assert "ignorato" in eng.skip(sid)
    assert "già gestito" in await eng.confirm(sid)


async def test_panic_closes_everything_and_blocks():
    eng, data, notifier, clock = _engine("paper", confirm=False)
    await eng.on_signal(_signal())
    assert len(eng.store.open_trades()) == 1
    out = await eng.panic()
    assert out and eng.store.open_trades() == []
    clock.t += 3_600_000
    assert not await eng.on_signal(_signal())
    assert "kill switch" in await eng.status_text()


async def test_daily_loss_halts_new_trades():
    eng, data, notifier, clock = _engine("paper", confirm=False, max_daily_loss_pct=1.0)
    await eng.monitor()  # registra il capitale di inizio giornata
    await eng.on_signal(_signal())
    data.price = 98.5
    await eng.monitor()  # stop colpito: -1% circa
    assert "Stop Loss colpito" in notifier.texts()
    clock.t += 3_600_000
    eng.store.set_state("paper_equity", "985")
    await eng.monitor()
    assert "perdita giornaliera" in notifier.texts()
    clock.t += 3_600_000
    assert not await eng.on_signal(_signal())


async def test_break_even_notification():
    eng, data, notifier, clock = _engine("paper", confirm=False)
    await eng.on_signal(_signal())
    data.price = 101.1
    await eng.monitor()
    assert "break-even" in notifier.texts()
    trade = eng.store.open_trades()[0]
    assert trade.moved_to_be
    data.price = 100.0
    await eng.monitor()
    assert eng.store.closed_trades()[0].exit_reason == "be"


async def test_scan_pipeline(monkeypatch):
    candles = [Candle(T0 + i * 300_000, 100, 100.5, 99.5, 100) for i in range(400)]
    eng, data, notifier, clock = _engine("signals")
    data.candles = candles
    monkeypatch.setattr(
        engine_mod, "detect", lambda *a, **k: [Setup("Viagra", Side.LONG, "test")]
    )
    sent = await eng.scan()
    assert len(sent) == 1 and sent[0].side is Side.LONG
    assert sent[0].stop_loss < sent[0].entry < sent[0].take_profit
    # la stessa candela non viene rianalizzata
    assert await eng.scan() == []


async def test_paused_engine_does_not_scan(monkeypatch):
    eng, data, notifier, clock = _engine("signals")
    eng.guard.set_paused(True)
    monkeypatch.setattr(engine_mod, "detect", lambda *a, **k: pytest.fail("non deve analizzare"))
    assert await eng.scan() == []
