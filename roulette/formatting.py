"""Messaggi Telegram (HTML) del roulette tracker."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from html import escape

from .config import RouletteConfig, TableConfig
from .store import Alert
from .tracker import Tracker, fmt_numbers, table_title
from .wheel import CATEGORIES, GROUPS, current_streak, gap, longest_streak, pocket_str

SOURCE_LABEL = {"manual": "manuale", "simulator": "simulato", "http_json": "feed JSON"}


def ago(ts_ms: int | None, now_ms: int) -> str:
    if ts_ms is None:
        return "nessun dato"
    sec = max(0, (now_ms - ts_ms) // 1000)
    if sec < 60:
        return "adesso"
    if sec < 3600:
        return f"{sec // 60} min fa"
    if sec < 86_400:
        return f"{sec // 3600} h fa"
    return f"{sec // 86_400} g fa"


def _streak_parts(numbers: Sequence[int], min_len: int = 1) -> list[tuple[int, str]]:
    parts = []
    for key, cat in CATEGORIES.items():
        n = current_streak(numbers, key)
        if n >= min_len:
            parts.append((n, f"{cat.label} ×{n}"))
    return parts


def tables_text(tracker: Tracker) -> str:
    now = tracker.now_ms()
    lines = ["🎰 <b>Tavoli monitorati</b>"]
    by_provider: dict[str, list[TableConfig]] = {}
    for t in tracker.cfg.tables:
        by_provider.setdefault(t.provider, []).append(t)
    for provider, tables in by_provider.items():
        lines.append(f"\n<b>{escape(provider)}</b>")
        for t in tables:
            numbers = tracker.recent[t.id]
            n, label = max((current_streak(numbers, k), CATEGORIES[k].label) for k in GROUPS["colore"])
            info = f" · serie {label} ×{n}" if n else ""
            lines.append(
                f"• {escape(t.name)} (<code>{t.id}</code>) · {SOURCE_LABEL[t.source]} · "
                f"{ago(tracker.last_ts[t.id], now)}"
            )
            if numbers:
                lines.append(f"   {fmt_numbers(numbers, 8)}{info}")
    return "\n".join(lines)


def last_text(table: TableConfig, numbers: Sequence[int]) -> str:
    if not numbers:
        return f"{table_title(table)}\nNessun giro registrato."
    return "\n".join(
        [
            f"{table_title(table)} — ultimi {len(numbers)} (dal più recente)",
            fmt_numbers(numbers, len(numbers)),
        ]
    )


def table_streaks_text(table: TableConfig, numbers: Sequence[int]) -> str:
    if not numbers:
        return f"{table_title(table)}\nNessun giro registrato."
    running = " · ".join(text for _, text in _streak_parts(numbers)) or "—"
    gaps = []
    for key, cat in CATEGORIES.items():
        g = gap(numbers, key)
        gaps.append(f"{cat.label} {g if g is not None else f'≥{len(numbers)}'}")
    return "\n".join(
        [
            f"📈 {table_title(table)}",
            f"Ultimi: {fmt_numbers(numbers, 10)}",
            "",
            f"<b>Serie in corso</b>\n{running}",
            "",
            f"<b>Ritardi</b> (giri senza uscire)\n{' · '.join(gaps)}",
        ]
    )


def all_streaks_text(tracker: Tracker, min_len: int = 3) -> str:
    rows = []
    for t in tracker.cfg.tables:
        parts = _streak_parts(tracker.recent[t.id], min_len)
        if parts:
            parts.sort(key=lambda p: -p[0])
            rows.append((parts[0][0], f"{table_title(t)}\n   " + " · ".join(p[1] for p in parts)))
    if not rows:
        return f"📈 Nessuna serie in corso di almeno {min_len} su nessun tavolo."
    rows.sort(key=lambda r: -r[0])
    return "\n".join([f"📈 <b>Serie in corso</b> (almeno {min_len} di fila)", *(r[1] for r in rows)])


def stats_text(table: TableConfig, numbers: Sequence[int]) -> str:
    total = len(numbers)
    if not total:
        return f"{table_title(table)}\nNessun giro registrato."

    def count(key: str) -> int:
        test = CATEGORIES[key].test
        return sum(1 for n in numbers if test(n))

    lines = [f"📊 {table_title(table)} — ultimi {total} giri", ""]
    for keys in GROUPS.values():
        lines.append(
            " · ".join(
                f"{CATEGORIES[k].label} {c} ({c / total:.0%})" for k in keys for c in [count(k)]
            )
        )
    lines.append("")
    lines.append(
        "<b>Serie più lunghe</b>\n"
        + " · ".join(f"{CATEGORIES[k].label} {longest_streak(numbers, k)}" for k in CATEGORIES)
    )
    freq = Counter(numbers)
    last_seen = {n: i for i, n in enumerate(numbers)}
    hot = sorted(freq, key=lambda n: (-freq[n], -last_seen[n]))[:6]
    # a parità di uscite, prima quelli assenti da più tempo
    cold = sorted(table.wheel.pockets, key=lambda n: (freq[n], last_seen.get(n, -1), n))[:6]
    lines.append("")
    lines.append("🔥 Caldi: " + " · ".join(f"{pocket_str(n)} ({freq[n]})" for n in hot))
    lines.append("🧊 Freddi: " + " · ".join(f"{pocket_str(n)} ({freq[n]})" for n in cold))
    return "\n".join(lines)


def scope_text(alert: Alert, cfg: RouletteConfig) -> str:
    if alert.scope == "table":
        t = cfg.table(alert.target)
        return f"{escape(t.name)} ({escape(t.provider)})" if t else f"tavolo {alert.target} (rimosso)"
    if alert.scope == "provider":
        return f"tavoli {escape(cfg.providers().get(alert.target, alert.target))}"
    return "tutti i tavoli"


def alert_text(alert: Alert, cfg: RouletteConfig) -> str:
    cat = CATEGORIES.get(alert.category)
    label = cat.label if cat else alert.category
    return f"{label} · {alert.length} di fila · {scope_text(alert, cfg)}"


def alerts_text(alerts: Sequence[Alert], cfg: RouletteConfig) -> str:
    if not alerts:
        return "🔕 Nessun alert. Creane uno con /alert."
    lines = ["🔔 <b>I tuoi alert</b>"]
    lines += [f"#{a.id} {alert_text(a, cfg)}" for a in alerts]
    return "\n".join(lines)
