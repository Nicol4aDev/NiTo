"""Setup ispirati alle tecniche di "Scalping The Bull".

Le regole sono ricostruite dalle descrizioni pubbliche degli indicatori
TradingView che accompagnano il canale (vedi docs/STRATEGIA.md): EMA 5/10/60/223,
sfondo verde con EMA60 > EMA223 e rosso nel caso opposto, trigger point
giornalieri. Dove la descrizione originale è discrezionale, qui c'è una scelta
precisa e documentata, così ogni segnale è riproducibile e testabile.

Ogni setup lavora solo su candele CHIUSE fino all'indice `i` incluso.
Le funzioni sono scritte dal punto di vista LONG e specchiate per lo SHORT.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import StrategyConfig
from .indicators import atr, ema
from .models import Candle, DailyLevels, Setup, Side

TREND_FOLLOWING = {
    "viagra",
    "fake_ema10",
    "third_touch_60",
    "third_touch_223",
    "shimano",
    "anti_crossing",
    "bomb",
}
# "emergence" e "downward_fake" anticipano un'inversione: il filtro trend non si applica.


@dataclass
class Series:
    candles: list[Candle]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    ema_fast: list[float | None]
    ema_signal: list[float | None]
    ema_mid: list[float | None]
    ema_slow: list[float | None]
    atr: list[float | None]
    warmup: int = 0  # primo indice con tutti gli indicatori disponibili

    @classmethod
    def build(cls, candles: Sequence[Candle], cfg: StrategyConfig) -> "Series":
        candles = list(candles)
        o = [c.open for c in candles]
        h = [c.high for c in candles]
        lo = [c.low for c in candles]
        c = [c.close for c in candles]
        warmup = max(cfg.ema_fast, cfg.ema_signal, cfg.ema_mid, cfg.ema_slow, cfg.atr_period) - 1
        return cls(
            candles=candles,
            open=o,
            high=h,
            low=lo,
            close=c,
            ema_fast=ema(c, cfg.ema_fast),
            ema_signal=ema(c, cfg.ema_signal),
            ema_mid=ema(c, cfg.ema_mid),
            ema_slow=ema(c, cfg.ema_slow),
            atr=atr(h, lo, c, cfg.atr_period),
            warmup=warmup,
        )

    def __len__(self) -> int:
        return len(self.close)

    def ready(self, i: int, lookback: int = 1) -> bool:
        """True se tutti gli indicatori sono disponibili da i-lookback a i."""
        start = i - lookback
        if start < self.warmup or i >= len(self):
            return False
        for arr in (self.ema_fast, self.ema_signal, self.ema_mid, self.ema_slow, self.atr):
            if arr[start] is None:
                return False
        return True

    def trend_up(self, i: int) -> bool:
        """Sfondo verde: EMA60 sopra EMA223."""
        return self.ema_mid[i] > self.ema_slow[i]  # type: ignore[operator]


# --- helper per scrivere le regole una volta sola (prospettiva long) ---


def _above(a: float, b: float, side: Side) -> bool:
    """`a` è "sopra" `b` nella direzione del trade (sotto, per lo short)."""
    return a > b if side is Side.LONG else a < b


def _near(s: Series, j: int, side: Side) -> float:
    """Estremo della candela verso la direzione del trade (high per il long)."""
    return s.high[j] if side is Side.LONG else s.low[j]


def _far(s: Series, j: int, side: Side) -> float:
    """Estremo opposto (low per il long)."""
    return s.low[j] if side is Side.LONG else s.high[j]


def _band_top(s: Series, j: int, side: Side) -> float:
    m, sl = s.ema_mid[j], s.ema_slow[j]
    return max(m, sl) if side is Side.LONG else min(m, sl)  # type: ignore[type-var]


def _gap_pct(s: Series, j: int) -> float:
    return abs(s.ema_mid[j] - s.ema_slow[j]) / s.ema_slow[j] * 100  # type: ignore[operator]


def _streak(s: Series, end: int, cond: Callable[[int], bool], limit: int) -> int:
    """Numero di candele consecutive, a ritroso da `end`, che soddisfano `cond`."""
    n = 0
    j = end
    while j >= s.warmup and n < limit and cond(j):
        n += 1
        j -= 1
    return n


def _bullish(s: Series, i: int, side: Side) -> bool:
    return _above(s.close[i], s.open[i], side)


# --- setup ---


def viagra(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    """Serie di chiusure sotto la EMA10, poi la rottura: possibile cambio di trend."""
    n = cfg.viagra_min_closes
    run = _streak(s, i - 1, lambda j: _above(s.ema_signal[j], s.close[j], side), n + 50)
    if run >= n and _above(s.close[i], s.ema_signal[i], side) and _bullish(s, i, side):
        where = "sotto" if side is Side.LONG else "sopra"
        return f"{run} chiusure {where} la EMA{cfg.ema_signal}, poi rottura"
    return None


def fake_ema10(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    """Apre sopra la EMA10, la buca con la spike e richiude sopra."""
    e = s.ema_signal[i]
    if (
        _above(s.open[i], e, side)
        and _above(e, _far(s, i, side), side)
        and _above(s.close[i], e, side)
        and _above(s.close[i], s.ema_mid[i], side)
    ):
        return f"falsa rottura della EMA{cfg.ema_signal} riassorbita"
    return None


def _third_touch(
    s: Series, i: int, cfg: StrategyConfig, side: Side, line: list[float | None], label: int
) -> str | None:
    """Tre (o più) tocchi da sotto della EMA rifiutati, poi la chiusura sopra."""
    if not _above(s.close[i], line[i], side):
        return None
    run = _streak(s, i - 1, lambda j: _above(line[j], s.close[j], side), cfg.touch_lookback)
    if run == 0:
        return None
    touches = 0
    prev_touch = False
    for j in range(i - run, i):
        touch = not _above(line[j], _near(s, j, side), side)  # il wick arriva alla EMA
        if touch and not prev_touch:
            touches += 1
        prev_touch = touch
    if touches >= cfg.touches_required:
        where = "sopra" if side is Side.LONG else "sotto"
        return f"{touches} tocchi della EMA{label} respinti, poi chiusura {where}"
    return None


def third_touch_60(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    return _third_touch(s, i, cfg, side, s.ema_mid, cfg.ema_mid)


def third_touch_223(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    return _third_touch(s, i, cfg, side, s.ema_slow, cfg.ema_slow)


def shimano(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    """Compressione: serie di chiusure tra EMA60 ed EMA223, poi uscita dalla banda."""

    def inside(j: int) -> bool:
        lo, hi = sorted((s.ema_mid[j], s.ema_slow[j]))  # type: ignore[type-var]
        return lo <= s.close[j] <= hi

    run = _streak(s, i - 1, inside, cfg.shimano_min_closes + 200)
    if run >= cfg.shimano_min_closes and _above(s.close[i], _band_top(s, i, side), side):
        return f"{run} chiusure tra EMA{cfg.ema_mid} ed EMA{cfg.ema_slow}, poi breakout"
    return None


def _cross_near(s: Series, i: int, cfg: StrategyConfig) -> bool:
    k = cfg.cross_lookback
    return _gap_pct(s, i) <= cfg.cross_gap_pct and _gap_pct(s, i) < _gap_pct(s, i - k)


def _fresh_break(s: Series, i: int, side: Side) -> bool:
    return _above(s.close[i], _band_top(s, i, side), side) and not _above(
        s.close[i - 1], _band_top(s, i - 1, side), side
    )


def emergence(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    """La EMA60 sta per incrociare al rialzo la EMA223 e il prezzo esce sopra entrambe."""
    if (
        _above(s.ema_slow[i], s.ema_mid[i], side)
        and _cross_near(s, i, cfg)
        and _fresh_break(s, i, side)
    ):
        return f"EMA{cfg.ema_mid} a {_gap_pct(s, i):.2f}% dall'incrocio con la EMA{cfg.ema_slow}"
    return None


def anti_crossing(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    """La EMA60 sta per incrociare al ribasso la EMA223 ma un impulso riporta il prezzo sopra."""
    if (
        _above(s.ema_mid[i], s.ema_slow[i], side)
        and _cross_near(s, i, cfg)
        and _fresh_break(s, i, side)
        and _bullish(s, i, side)
    ):
        return "incrocio contrario evitato da un impulso"
    return None


def downward_fake(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    """Dopo un Death Cross il prezzo torna sopra la EMA223 rompendo il massimo di ieri."""
    trigger = lv.prev_high if side is Side.LONG else lv.prev_low
    if trigger is None:
        return None
    if not (
        _above(s.close[i], s.ema_slow[i], side)
        and _above(s.close[i], trigger, side)
        and not _above(s.close[i - 1], trigger, side)
    ):
        return None
    start = max(s.warmup + 1, i - cfg.fake_cross_lookback + 1)
    for j in range(i, start - 1, -1):
        # incrocio contrario alla direzione del trade (Death Cross per il long)
        if not _above(s.ema_slow[j - 1], s.ema_mid[j - 1], side) and _above(
            s.ema_slow[j], s.ema_mid[j], side
        ):
            level = "massimo" if side is Side.LONG else "minimo"
            return f"incrocio contrario {i - j} candele fa smentito, rotto il {level} di ieri"
    return None


def bomb(s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, side: Side) -> str | None:
    """Candela esplosiva (>= soglia %) che attraversa un trigger point giornaliero."""
    change = (s.close[i] - s.open[i]) / s.open[i] * 100 * side.sign
    if change < cfg.bomb_threshold_pct:
        return None
    names = {"apertura di oggi": lv.today_open, "massimo di ieri": lv.prev_high, "minimo di ieri": lv.prev_low}
    for name, level in names.items():
        if level is None:
            continue
        if not _above(s.open[i], level, side) and not _above(level, s.close[i], side):
            return f"candela {change:+.2f}% attraverso {name}"
    return None


SETUPS: dict[str, tuple[str, Callable[..., str | None]]] = {
    "viagra": ("Viagra", viagra),
    "fake_ema10": ("Fake EMA10", fake_ema10),
    "third_touch_60": ("Terzo tocco EMA60", third_touch_60),
    "third_touch_223": ("Terzo tocco EMA223", third_touch_223),
    "shimano": ("Shimano", shimano),
    "emergence": ("Emergence", emergence),
    "anti_crossing": ("Anti-crossing", anti_crossing),
    "downward_fake": ("Downward fake", downward_fake),
    "bomb": ("Bomb", bomb),
}

MIN_HISTORY = 2  # candele precedenti necessarie con indicatori pronti


def detect(
    s: Series, i: int, lv: DailyLevels, cfg: StrategyConfig, allow_short: bool = True
) -> list[Setup]:
    lookback = max(MIN_HISTORY, cfg.cross_lookback)
    if not s.ready(i, lookback):
        return []
    trend_up = s.trend_up(i)
    sides = [Side.LONG, Side.SHORT] if allow_short and cfg.allow_short else [Side.LONG]
    found: list[Setup] = []
    for key in cfg.enabled:
        label, fn = SETUPS[key]
        for side in sides:
            if cfg.trend_filter and key in TREND_FOLLOWING and trend_up != (side is Side.LONG):
                continue
            reason = fn(s, i, lv, cfg, side)
            if reason:
                found.append(Setup(label, side, reason))
    return found


def pick_side(setups: list[Setup], min_confluence: int = 1) -> Side | None:
    """Sceglie la direzione; segnali discordanti sulla stessa candela si annullano."""
    longs = sum(1 for s in setups if s.side is Side.LONG)
    shorts = sum(1 for s in setups if s.side is Side.SHORT)
    if longs and shorts:
        return None
    if longs >= min_confluence:
        return Side.LONG
    if shorts >= min_confluence:
        return Side.SHORT
    return None
