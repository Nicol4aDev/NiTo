from nito.indicators import atr, ema
from nito.levels import levels_from_daily, levels_from_intraday
from nito.models import Candle

from .conftest import DAY, T0


def test_ema_seed_and_recursion():
    out = ema([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == 2.0  # SMA dei primi 3
    assert out[3] == 4 * 0.5 + 2.0 * 0.5
    assert out[4] == 5 * 0.5 + out[3] * 0.5


def test_ema_too_short():
    assert ema([1, 2], 5) == [None, None]


def test_atr_constant_range():
    h = [11.0] * 20
    lo = [9.0] * 20
    c = [10.0] * 20
    out = atr(h, lo, c, 14)
    assert out[12] is None
    assert out[13] == 2.0
    assert out[-1] == 2.0


def _c(ts, o, h, l, c):
    return Candle(ts, o, h, l, c)


def test_levels_from_intraday_no_lookahead():
    hour = 3_600_000
    candles = [
        _c(T0 - 2 * hour, 50, 60, 40, 55),  # giorno parziale: ignorato
        _c(T0, 100, 110, 95, 105),
        _c(T0 + hour, 105, 120, 101, 118),
        _c(T0 + DAY, 118, 119, 117, 118),
        _c(T0 + DAY + hour, 118, 125, 116, 124),
    ]
    lv = levels_from_intraday(candles)
    assert lv[0].today_open is None and lv[0].prev_high is None
    assert lv[1].today_open == 100 and lv[1].prev_high is None  # ieri era parziale
    assert lv[3].today_open == 118
    assert (lv[3].prev_high, lv[3].prev_low) == (120, 95)
    assert (lv[4].prev_high, lv[4].prev_low) == (120, 95)


def test_levels_from_daily():
    daily = [_c(T0 - DAY, 90, 120, 80, 100), _c(T0, 100, 105, 99, 101)]
    lv = levels_from_daily(daily, T0 + 5 * 3_600_000)
    assert (lv.today_open, lv.prev_high, lv.prev_low) == (100, 120, 80)
    # se manca la candela di oggi, nessun livello inventato
    lv = levels_from_daily(daily[:1], T0 + DAY + 1)
    assert lv.today_open is None and lv.prev_high is None
