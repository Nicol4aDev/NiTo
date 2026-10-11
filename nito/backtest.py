"""Backtest delle strategie su dati storici, con le stesse regole del bot.

Esempi:
    python -m nito.backtest --symbol SOL/USDT:USDT --timeframe 5m --days 30
    python -m nito.backtest --csv dati.csv --timeframe 5m

Ipotesi (conservative):
- entrata al prezzo di chiusura della candela del segnale;
- se nella stessa candela sono raggiungibili sia SL sia TP, vince lo SL;
- lo spostamento a break-even vale dalla candela successiva;
- commissioni `risk.fee_pct` su entrata e uscita.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Config, ConfigError, config_from_dict, load_config
from .levels import levels_from_intraday
from .market_data import timeframe_ms
from .models import Candle, Side, Trade, TradeStatus
from .risk import position_size
from .signals import build_signal
from .simulation import step
from .stats import Stats, compute_stats
from .strategy import Series, detect, pick_side


@dataclass
class BacktestResult:
    trades: list[Trade]
    stats: Stats
    by_setup: dict[str, Stats]
    rejected: Counter = field(default_factory=Counter)
    final_equity: float = 0.0


def run_backtest(
    candles: Sequence[Candle],
    cfg: Config,
    symbol: str = "TEST",
    btc_candles: Sequence[Candle] | None = None,
) -> BacktestResult:
    st, r = cfg.strategy, cfg.risk
    tf = cfg.market.timeframe
    tf_ms = timeframe_ms(tf)
    s = Series.build(candles, st)
    levels = levels_from_intraday(candles)
    allow_short = st.allow_short and cfg.market.market_type == "swap"

    btc_trend: dict[int, bool] = {}
    if btc_candles is not None and cfg.market.btc_filter:
        bs = Series.build(btc_candles, st)
        for j, c in enumerate(btc_candles):
            if bs.ready(j, 0):
                btc_trend[c.ts] = bs.trend_up(j)

    equity = r.account_equity
    trades: list[Trade] = []
    rejected: Counter = Counter()
    current: Trade | None = None
    last_signal_ts: int | None = None
    cooldown_ms = r.cooldown_minutes * 60_000

    for i, c in enumerate(s.candles):
        if current is not None:
            step(current, c.open, c.high, c.low, c.ts + tf_ms, r.fee_pct)
            if current.status is TradeStatus.CLOSED:
                equity += current.pnl or 0.0
                trades.append(current)
                current = None
            continue

        setups = detect(s, i, levels[i], st, allow_short)
        side = pick_side(setups, st.min_confluence)
        if side is None:
            continue
        if btc_trend and c.ts in btc_trend and btc_trend[c.ts] != (side is Side.LONG):
            rejected["BTC in trend opposto"] += 1
            continue
        if last_signal_ts is not None and c.ts - last_signal_ts < cooldown_ms:
            rejected["cooldown"] += 1
            continue
        chosen = [x for x in setups if x.side is side]
        sig, reason = build_signal(s, i, side, chosen, r, symbol, tf)
        if sig is None:
            rejected[reason.split(": ")[0]] += 1
            continue
        qty = position_size(equity, r.risk_per_trade_pct, sig.entry, sig.stop_loss, r.fee_pct, r.max_leverage)
        if qty <= 0:
            rejected["capitale esaurito"] += 1
            continue
        last_signal_ts = c.ts
        current = Trade(
            symbol=symbol,
            side=side,
            qty=qty,
            entry=sig.entry,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            break_even_trigger=sig.break_even_trigger,
            opened_at=c.ts + tf_ms,
        )
        current.extra["setups"] = [x.name for x in chosen]

    by_setup: dict[str, list[Trade]] = {}
    for t in trades:
        for name in t.extra.get("setups", []):
            by_setup.setdefault(name, []).append(t)
    return BacktestResult(
        trades=trades,
        stats=compute_stats(trades, r.account_equity),
        by_setup={k: compute_stats(v, r.account_equity) for k, v in sorted(by_setup.items())},
        rejected=rejected,
        final_equity=equity,
    )


# ------------------------------------------------------------------ dati


def load_csv(path: str | Path) -> list[Candle]:
    """CSV con colonne timestamp,open,high,low,close[,volume]; timestamp in ms o ISO 8601."""
    candles = []
    with open(path, newline="") as fh:
        for row in csv.reader(fh):
            if not row or not row[0].strip() or row[0].strip().lower() in ("timestamp", "time", "date"):
                continue
            raw = row[0].strip()
            if raw.isdigit():
                ts = int(raw)
                if ts < 10**11:  # secondi
                    ts *= 1000
            else:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = int(dt.timestamp() * 1000)
            vol = float(row[5]) if len(row) > 5 and row[5] else 0.0
            candles.append(Candle(ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]), vol))
    candles.sort(key=lambda c: c.ts)
    return candles


def download(exchange_id: str, market_type: str, symbol: str, timeframe: str, days: int) -> list[Candle]:
    import ccxt

    ex = getattr(ccxt, exchange_id)({"enableRateLimit": True, "options": {"defaultType": market_type}})
    tf_ms = timeframe_ms(timeframe)
    since = int(time.time() * 1000) - days * 86_400_000
    out: dict[int, Candle] = {}
    now = int(time.time() * 1000)
    while since < now:
        rows = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not rows:
            break
        for row in rows:
            if row[0] + tf_ms <= now:
                out[int(row[0])] = Candle.from_ccxt(row)
        nxt = int(rows[-1][0]) + tf_ms
        if nxt <= since:
            break
        since = nxt
    return [out[k] for k in sorted(out)]


# ------------------------------------------------------------------ CLI


def print_report(res: BacktestResult, cfg: Config, symbol: str, candles: Sequence[Candle]) -> None:
    start = datetime.fromtimestamp(candles[0].ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    end = datetime.fromtimestamp(candles[-1].ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"\n=== Backtest {symbol} {cfg.market.timeframe} · {start} → {end} · {len(candles)} candele ===")
    for line in res.stats.as_lines():
        print("  " + line)
    print(f"  Capitale: {cfg.risk.account_equity:.2f} → {res.final_equity:.2f}")
    if res.by_setup:
        print("\n  Per setup:")
        print(f"  {'setup':<20}{'trade':>7}{'win%':>8}{'media R':>9}{'PF':>7}")
        for name, st in res.by_setup.items():
            pf = st.profit_factor_text
            print(f"  {name:<20}{st.trades:>7}{st.win_rate:>7.1f}%{st.avg_r:>+9.2f}{pf:>7}")
    if res.rejected:
        print("\n  Segnali scartati:")
        for reason, n in res.rejected.most_common():
            print(f"    {n:>5} × {reason}")
    print(
        "\nI risultati passati non garantiscono quelli futuri. Valuta almeno 100 operazioni "
        "e più periodi/mercati prima di fidarti di un setup."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nito.backtest", description="Backtest delle strategie NiTo")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--symbol", help="simbolo ccxt, es. SOL/USDT:USDT")
    p.add_argument("--timeframe", help="es. 1m, 5m, 15m (default: quello del config)")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--csv", help="file CSV di candele invece del download")
    args = p.parse_args(argv)

    try:
        cfg = load_config(args.config) if Path(args.config).exists() else config_from_dict({})
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.timeframe:
        cfg.market.timeframe = args.timeframe

    symbols = [args.symbol] if args.symbol else cfg.market.symbols
    for symbol in symbols:
        if args.csv:
            candles = load_csv(args.csv)
            btc = None
        else:
            candles = download(cfg.market.exchange, cfg.market.market_type, symbol, cfg.market.timeframe, args.days)
            btc = None
            if cfg.market.btc_filter and symbol != cfg.market.btc_symbol:
                btc = download(
                    cfg.market.exchange, cfg.market.market_type, cfg.market.btc_symbol,
                    cfg.market.timeframe, args.days,
                )
        if len(candles) < cfg.strategy.ema_slow + 50:
            print(f"{symbol}: dati insufficienti ({len(candles)} candele)", file=sys.stderr)
            continue
        res = run_backtest(candles, cfg, symbol, btc)
        print_report(res, cfg, symbol, candles)
        if args.csv:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
