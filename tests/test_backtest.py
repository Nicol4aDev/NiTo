import random

from nito.backtest import load_csv, run_backtest
from nito.config import config_from_dict
from nito.models import Candle

from .conftest import T0


def _random_walk(n=6000, seed=3):
    rnd = random.Random(seed)
    price, out, drift = 100.0, [], 0.0
    for i in range(n):
        if i % 600 == 0:
            drift = rnd.choice([-1, 1]) * rnd.uniform(0.0002, 0.0008)
        o = price
        c = o * (1 + drift + rnd.gauss(0, 0.003))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.0015)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.0015)))
        out.append(Candle(T0 + i * 300_000, o, h, lo, c))
        price = c
    return out


def test_backtest_is_consistent():
    cfg = config_from_dict({})
    candles = _random_walk()
    res = run_backtest(candles, cfg)
    assert res.stats.trades > 0
    assert res.stats.trades == res.stats.wins + res.stats.losses + res.stats.breakeven
    assert abs(res.final_equity - (cfg.risk.account_equity + res.stats.total_pnl)) < 1e-6
    for t in res.trades:
        # nessuna perdita oltre ~1R (lo stop è sempre rispettato, salvo gap)
        assert t.r_multiple > -1.5
        assert t.opened_at > 0 and t.closed_at >= t.opened_at
    # trade sequenziali: mai due posizioni contemporanee sullo stesso simbolo
    for a, b in zip(res.trades, res.trades[1:]):
        assert b.opened_at >= a.closed_at


def test_backtest_deterministic():
    cfg = config_from_dict({})
    candles = _random_walk(seed=5)
    a = run_backtest(candles, cfg)
    b = run_backtest(candles, cfg)
    assert [t.pnl for t in a.trades] == [t.pnl for t in b.trades]


def test_load_csv(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-10-10T00:05:00Z,2,3,1,2.5,10\n"
        "1760054400,1,2,0.5,1.5,5\n"
    )
    candles = load_csv(p)
    assert [c.close for c in candles] == [1.5, 2.5]
    assert candles[0].ts == 1_760_054_400_000
