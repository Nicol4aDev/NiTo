from nito.config import StrategyConfig
from nito.models import DailyLevels, Setup, Side
from nito.strategy import detect, pick_side

from .conftest import make_series

NOLV = DailyLevels()


def names(setups):
    return {s.name for s in setups}


def test_viagra_long_after_streak_below_ema10(strat):
    close = [106, 106, 104, 104, 104, 104, 104, 106]
    open_ = close[:-1] + [104.5]
    s = make_series(close, open_=open_, e10=105, e60=100, e223=90)
    found = detect(s, len(s) - 1, NOLV, strat)
    assert "Viagra" in names(found)
    assert all(x.side is Side.LONG for x in found)


def test_viagra_needs_enough_closes(strat):
    close = [106, 106, 106, 104, 104, 104, 104, 106]
    open_ = close[:-1] + [104.5]
    s = make_series(close, open_=open_, e10=105, e60=100, e223=90)
    assert "Viagra" not in names(detect(s, len(s) - 1, NOLV, strat))


def test_viagra_short_mirror(strat):
    close = [94, 94, 96, 96, 96, 96, 96, 94]
    open_ = close[:-1] + [95.5]
    s = make_series(close, open_=open_, e10=95, e60=90, e223=100)
    found = detect(s, len(s) - 1, NOLV, strat)
    assert ("Viagra", Side.SHORT) in {(x.name, x.side) for x in found}


def test_fake_ema10_long(strat):
    close = [106] * 7 + [106]
    open_ = [106] * 7 + [105.5]
    low = [105.8] * 7 + [104.5]
    s = make_series(close, open_=open_, low=low, e10=105, e60=100, e223=90)
    assert names(detect(s, 7, NOLV, strat)) == {"Fake EMA10"}


def test_trend_filter_blocks_counter_trend(strat):
    close = [106] * 7 + [106]
    open_ = [106] * 7 + [105.5]
    low = [105.8] * 7 + [104.5]
    # sfondo rosso: EMA60 < EMA223
    s = make_series(close, open_=open_, low=low, e10=105, e60=100, e223=110)
    assert detect(s, 7, NOLV, strat) == []
    no_filter = StrategyConfig(trend_filter=False)
    assert "Fake EMA10" in names(detect(s, 7, NOLV, no_filter))


def test_third_touch_ema60(strat):
    n = 11
    close = [98.0] * (n - 1) + [101.0]
    open_ = [98.0] * (n - 1) + [98.5]
    high = [98.5] * n
    for j, h in [(2, 100.2), (3, 100.1), (5, 100.0), (8, 100.3)]:  # 2 e 3 = un solo tocco
        high[j] = h
    high[-1] = 101.2
    s = make_series(close, open_=open_, high=high, e10=97, e60=100, e223=90)
    found = detect(s, n - 1, NOLV, strat)
    assert "Terzo tocco EMA60" in names(found)

    high[8] = 99.0  # solo due tocchi
    s = make_series(close, open_=open_, high=high, e10=97, e60=100, e223=90)
    assert "Terzo tocco EMA60" not in names(detect(s, n - 1, NOLV, strat))


def test_shimano_breakout(strat):
    close = [97.0] * 9 + [101.0]
    open_ = [97.0] * 9 + [97.5]
    s = make_series(close, open_=open_, e10=96, e60=100, e223=95)
    assert "Shimano" in names(detect(s, 9, NOLV, strat))


def test_emergence_long_ignores_trend_filter(strat):
    e60 = [99.4, 99.5, 99.6, 99.7, 99.8, 99.85, 99.88, 99.9]
    close = [99.0] * 7 + [100.5]
    open_ = [99.0] * 7 + [99.2]
    s = make_series(close, open_=open_, e10=99.5, e60=e60, e223=100.0)
    found = detect(s, 7, NOLV, strat)
    assert "Emergence" in names(found)
    assert all(x.side is Side.LONG for x in found)


def test_anti_crossing_long(strat):
    e60 = [100.6, 100.5, 100.4, 100.3, 100.25, 100.2, 100.15, 100.1]
    close = [100.5, 100.4, 100.3, 100.2, 100.1, 100.0, 99.9, 100.6]
    open_ = close[:-1] + [100.0]
    s = make_series(close, open_=open_, e10=100.3, e60=e60, e223=100.0)
    assert "Anti-crossing" in names(detect(s, 7, NOLV, strat))


def test_downward_fake_long(strat):
    e60 = [101.0] * 4 + [99.0] * 6
    close = [99.0] * 9 + [101.0]
    open_ = [99.0] * 9 + [99.5]
    s = make_series(close, open_=open_, e10=99.5, e60=e60, e223=100.0)
    lv = DailyLevels(prev_high=100.8)
    assert "Downward fake" in names(detect(s, 9, lv, strat))
    # senza il massimo di ieri non c'è trigger
    assert "Downward fake" not in names(detect(s, 9, NOLV, strat))


def test_bomb_through_trigger_point(strat):
    close = [100.0] * 7 + [102.5]
    s = make_series(close, open_=[100.0] * 8, e10=99, e60=98, e223=97)
    lv = DailyLevels(today_open=101.0)
    assert "Bomb" in names(detect(s, 7, lv, strat))
    assert "Bomb" not in names(detect(s, 7, DailyLevels(today_open=105.0), strat))


def test_needs_history(strat):
    s = make_series([100.0] * 3, e10=99, e60=98, e223=97)
    assert detect(s, 2, NOLV, strat) == []


def test_pick_side():
    long_ = Setup("A", Side.LONG, "")
    short = Setup("B", Side.SHORT, "")
    assert pick_side([long_]) is Side.LONG
    assert pick_side([short]) is Side.SHORT
    assert pick_side([long_, short]) is None
    assert pick_side([long_], min_confluence=2) is None
    assert pick_side([]) is None


def test_rules_never_read_indicators_before_warmup():
    """Subito dopo il warm-up le serie di chiusure non devono leggere EMA mancanti."""
    from nito.models import Candle
    from nito.strategy import Series

    cfg = StrategyConfig()
    # prezzo piatto e poi breakout: tutte le regole "a serie" risalgono all'indietro
    candles = [Candle(i * 300_000, 100, 100.05, 99.95, 100) for i in range(300)]
    candles += [Candle(300 * 300_000, 100, 103, 100, 103)]
    s = Series.build(candles, cfg)
    for i in range(len(s)):
        detect(s, i, DailyLevels(prev_high=101, prev_low=99, today_open=100), cfg)
