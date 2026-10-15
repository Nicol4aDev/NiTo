"""Interfaccia Telegram del roulette tracker: comandi, alert guidati con pulsanti, job."""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from html import escape
from typing import Any

import httpx
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import RouletteConfig, TableConfig, slugify
from .formatting import (
    alert_text,
    alerts_text,
    all_streaks_text,
    last_text,
    stats_text,
    table_streaks_text,
    tables_text,
)
from .sources import HttpJsonSource, Simulator
from .store import Alert, Store
from .tracker import Notification, Tracker, fmt_numbers, table_title
from .wheel import CATEGORIES, GROUPS, Category, find_category, fmt_pocket, parse_pocket

log = logging.getLogger(__name__)

Buttons = list[list[tuple[str, str]]]

MIN_LENGTH, MAX_LENGTH = 2, 50
LENGTH_CHOICES = (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 20)
DEFAULT_LAST, MAX_LAST = 20, 200
DEFAULT_STATS, MAX_STATS = 200, 10_000

HELP = """<b>Roulette tracker</b>
/tavoli — tavoli monitorati, ultimi numeri, serie di colore
/ultimi [tavolo] [n] — ultimi n numeri usciti
/serie [tavolo] — serie in corso (su tutti i tavoli o su uno) e ritardi
/stats [tavolo] [n] — frequenze, serie più lunghe, numeri caldi e freddi
/alert — crea un alert con i pulsanti, oppure in una riga:
   <code>/alert rosso 6</code> · <code>/alert nero 8 evolution</code> · <code>/alert dispari 5 lightning</code>
/alerts — i tuoi alert, con i pulsanti per eliminarli
/add tavolo numeri — registra risultati a mano (dal più vecchio): <code>/add evo-lightning 17 0 32</code>
/inserisci [tavolo] — inserimento rapido: ogni numero che scrivi viene registrato
/fine — esce dall'inserimento rapido
/annulla [tavolo] — cancella l'ultimo numero inserito a mano

Categorie: rosso, nero, verde, pari, dispari, basso (1-18), alto (19-36), d1-d3 (dozzine), c1-c3 (colonne).
<i>Ogni giro è indipendente: una serie lunga non rende più probabile il colore opposto.</i>"""

COMMANDS = [
    BotCommand("tavoli", "Tavoli monitorati"),
    BotCommand("ultimi", "Ultimi numeri di un tavolo"),
    BotCommand("serie", "Serie in corso"),
    BotCommand("stats", "Statistiche di un tavolo"),
    BotCommand("alert", "Crea un alert sulle serie"),
    BotCommand("alerts", "I tuoi alert"),
    BotCommand("add", "Registra numeri a mano"),
    BotCommand("inserisci", "Inserimento rapido dei numeri"),
    BotCommand("fine", "Esci dall'inserimento rapido"),
    BotCommand("annulla", "Cancella l'ultimo numero inserito"),
    BotCommand("help", "Aiuto"),
]


@dataclass
class Runtime:
    cfg: RouletteConfig
    store: Store
    tracker: Tracker
    http: httpx.AsyncClient | None = None
    feeds: dict[str, HttpJsonSource] = field(default_factory=dict)
    simulators: dict[str, Simulator] = field(default_factory=dict)


def create_runtime(
    cfg: RouletteConfig, store: Store, clock: Callable[[], float] = time.time
) -> Runtime:
    rt = Runtime(cfg, store, Tracker(cfg, store, clock))
    for t in cfg.tables:
        if t.source == "simulator":
            rt.simulators[t.id] = Simulator(t)
        elif t.source == "http_json":
            if rt.http is None:
                rt.http = httpx.AsyncClient(
                    headers={"User-Agent": "nito-roulette/0.1"}, follow_redirects=True
                )
            rt.feeds[t.id] = HttpJsonSource(t, rt.http)
    return rt


# --- ricerca di tavoli, provider, categorie ---


def find_table(cfg: RouletteConfig, text: str) -> TableConfig | None:
    """Per id esatto, nome esatto, oppure per inizio dell'id / parte del nome se univoco."""
    q = text.strip().lower()
    if not q:
        return None
    for t in cfg.tables:
        if t.id == q:
            return t
    for candidates in (
        [t for t in cfg.tables if t.name.lower() == q],
        [t for t in cfg.tables if t.id.startswith(q) or q in t.name.lower()],
    ):
        if len(candidates) == 1:
            return candidates[0]
    return None


def find_provider(cfg: RouletteConfig, text: str) -> str | None:
    slug = slugify(text)
    return slug if slug in cfg.providers() else None


def parse_numbers(table: TableConfig, tokens: list[str]) -> list[int]:
    numbers = []
    for token in tokens:
        for part in token.replace(";", ",").split(","):
            if part.strip():
                numbers.append(parse_pocket(part, table.wheel))
    if not numbers:
        raise ValueError("nessun numero")
    return numbers


def parse_alert(cfg: RouletteConfig, args: list[str]) -> tuple[str, int, str, str]:
    """`/alert rosso 6 [tavolo|provider]` → (categoria, lunghezza, scope, target)."""
    if len(args) < 2:
        raise ValueError(
            "Uso: /alert categoria quanti [tavolo o provider]\n"
            "Esempio: <code>/alert rosso 6 evolution</code>. Scrivi solo /alert per i pulsanti."
        )
    first, second, rest = args[0], args[1], args[2:]
    if first.isdigit() and not second.isdigit():
        first, second = second, first
    cat = _find_category_or_error(first)
    if not second.isdigit() or not MIN_LENGTH <= int(second) <= MAX_LENGTH:
        raise ValueError(f"Il numero di uscite di fila deve essere tra {MIN_LENGTH} e {MAX_LENGTH}.")
    query = " ".join(rest).strip()
    if not query or query.lower() in ("tutti", "all", "ovunque"):
        return cat.key, int(second), "all", ""
    provider = find_provider(cfg, query)
    if provider:
        return cat.key, int(second), "provider", provider
    table = find_table(cfg, query)
    if table:
        return cat.key, int(second), "table", table.id
    raise ValueError(f"Nessun tavolo o provider corrisponde a «{escape(query)}». Vedi /tavoli.")


def _find_category_or_error(text: str) -> Category:
    cat = find_category(text)
    if cat is None:
        raise ValueError(
            f"Categoria «{escape(text)}» sconosciuta. Usa: " + ", ".join(CATEGORIES) + "."
        )
    return cat


def create_alert(rt: Runtime, chat_id: int, key: str, length: int, scope: str, target: str) -> str:
    cfg = rt.cfg
    if key not in CATEGORIES or not MIN_LENGTH <= length <= MAX_LENGTH:
        return "Alert non valido."
    if scope == "table" and cfg.table(target) is None:
        return "Tavolo non trovato."
    if scope == "provider" and target not in cfg.providers():
        return "Provider non trovato."
    if len(rt.store.alerts(chat_id)) >= cfg.max_alerts_per_chat:
        return f"Hai già {cfg.max_alerts_per_chat} alert: eliminane qualcuno con /alerts."
    alert_id = rt.store.add_alert(chat_id, key, length, scope, target, rt.tracker.now_ms())
    if alert_id is None:
        return "Hai già un alert identico. Vedi /alerts."
    alert = Alert(alert_id, chat_id, key, length, scope, target)
    return f"✅ Alert #{alert_id} creato: {alert_text(alert, cfg)}\nTi scrivo quando succede."


# --- procedura guidata (callback "al:…") ---


def category_buttons() -> Buttons:
    rows = [[(CATEGORIES[k].label, f"al:k:{k}") for k in keys] for keys in GROUPS.values()]
    return rows + [[("Annulla", "al:x")]]


def wizard(rt: Runtime, chat_id: int, data: str) -> tuple[str, Buttons | None]:
    """Passi: categoria (k) → quante di fila (n) → [tavolo (t)] → salva (s)."""
    parts = data.split(":")[1:]
    step = parts[0] if parts else ""
    if step == "x":
        return "Annullato.", None
    if step == "start":
        return "🔔 <b>Nuovo alert</b>\nQuale risultato vuoi seguire?", category_buttons()

    cat = CATEGORIES.get(parts[1]) if len(parts) > 1 else None
    if cat is None:
        return "Categoria non valida.", None
    if step == "k":
        rows = [
            [(str(n), f"al:n:{cat.key}:{n}") for n in LENGTH_CHOICES[i : i + 4]]
            for i in range(0, len(LENGTH_CHOICES), 4)
        ]
        text = (
            f"{cat.label}: dopo quante uscite di fila ti avviso?\n"
            f"<i>Per altri valori: /alert {cat.key} 13</i>"
        )
        return text, rows + [[("Annulla", "al:x")]]

    try:
        length = int(parts[2])
    except (IndexError, ValueError):
        return "Valore non valido.", None
    base = f"al:s:{cat.key}:{length}"
    if step == "n":
        rows: Buttons = [[("🌐 Tutti i tavoli", f"{base}:a")]]
        providers = list(rt.cfg.providers().items())
        rows += [
            [(name, f"{base}:p:{slug}") for slug, name in providers[i : i + 2]]
            for i in range(0, len(providers), 2)
        ]
        rows += [[("🎯 Un solo tavolo…", f"al:t:{cat.key}:{length}")], [("Annulla", "al:x")]]
        return f"{cat.label} · {length} di fila: su quali tavoli?", rows
    if step == "t":
        rows = [[(f"{t.name} · {t.provider}", f"{base}:t:{t.id}")] for t in rt.cfg.tables]
        return f"{cat.label} · {length} di fila: scegli il tavolo", rows + [[("Annulla", "al:x")]]
    if step == "s" and len(parts) > 3:
        scope = {"a": "all", "p": "provider", "t": "table"}.get(parts[3])
        target = parts[4] if len(parts) > 4 else ""
        if scope is None:
            return "Valore non valido.", None
        return create_alert(rt, chat_id, cat.key, length, scope, target), None
    return "Valore non valido.", None


def alerts_view(rt: Runtime, chat_id: int) -> tuple[str, Buttons | None]:
    alerts = rt.store.alerts(chat_id)
    buttons = [
        [(f"🗑 #{a.id}", f"del:{a.id}") for a in alerts[i : i + 4]] for i in range(0, len(alerts), 4)
    ]
    return alerts_text(alerts, rt.cfg), buttons or None


def table_picker(tables: list[TableConfig], action: str) -> Buttons:
    return [
        [(f"{t.name} · {t.provider}", f"{action}:{t.id}") for t in tables[i : i + 2]]
        for i in range(0, len(tables), 2)
    ]


# --- inserimento manuale ---


def record(rt: Runtime, table: TableConfig, numbers: list[int]) -> tuple[str, list[Notification]]:
    if not table.is_manual:
        text = (
            f"{table_title(table)} riceve i risultati in automatico: l'inserimento a mano "
            "è solo per i tavoli con source = \"manual\"."
        )
        return text, []
    _, notes = rt.tracker.add_spins(table.id, [(n, None) for n in numbers])
    text = (
        f"✅ {table_title(table)}: {' '.join(fmt_pocket(n) for n in numbers)}\n"
        f"Ultimi: {fmt_numbers(rt.tracker.recent[table.id], 10)}"
    )
    return text, notes


def undo(rt: Runtime, table: TableConfig) -> str:
    if not table.is_manual:
        return f"{table_title(table)} non è un tavolo manuale."
    number = rt.tracker.undo(table.id)
    if number is None:
        return f"{table_title(table)}: nessun numero da cancellare."
    return (
        f"↩️ {table_title(table)}: cancellato {fmt_pocket(number)}\n"
        f"Ultimi: {fmt_numbers(rt.tracker.recent[table.id], 10)}"
    )


def start_insert(chat_data: MutableMapping[str, Any], table: TableConfig) -> str:
    if not table.is_manual:
        return f"{table_title(table)} riceve i risultati in automatico."
    chat_data["insert"] = table.id
    return (
        f"⌨️ Inserimento rapido su {table_title(table)}.\n"
        "Scrivi i numeri appena escono (anche più di uno, dal più vecchio: <code>17 0 32</code>).\n"
        "/annulla cancella l'ultimo, /fine per uscire."
    )


# --- Telegram ---

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def _is_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cfg: RouletteConfig = context.application.bot_data["cfg"]
    user = update.effective_user
    return user is not None and user.id in cfg.allowed_user_ids


def authorized(func: Handler) -> Handler:
    """Solo gli user id in ROULETTE_ALLOWED_USERS possono usare comandi e pulsanti."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update, context):
            uid = update.effective_user.id if update.effective_user else "?"
            log.warning("accesso negato all'utente %s", uid)
            if update.callback_query:
                await update.callback_query.answer("⛔ Non autorizzato", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(
                    f"⛔ Non autorizzato. Il tuo user id è {uid}: aggiungilo a "
                    "ROULETTE_ALLOWED_USERS se questo è il tuo bot."
                )
            return
        await func(update, context)

    return wrapper


def _rt(context: ContextTypes.DEFAULT_TYPE) -> Runtime:
    return context.application.bot_data["rt"]


def _markup(buttons: Buttons | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in buttons]
    )


async def _reply(update: Update, text: str, buttons: Buttons | None = None) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=_markup(buttons),
            disable_web_page_preview=True,
        )


async def deliver(bot: Any, notes: list[Notification]) -> None:
    for note in notes:
        try:
            await bot.send_message(chat_id=note.chat_id, text=note.text, parse_mode=ParseMode.HTML)
        except TelegramError as exc:
            log.warning("alert non inviato alla chat %s: %s", note.chat_id, exc)


def _split_count(args: list[str], default: int, maximum: int) -> tuple[str, int]:
    """"lightning roulette 50" → ("lightning roulette", 50)."""
    if args and args[-1].isdigit():
        return " ".join(args[:-1]), max(1, min(int(args[-1]), maximum))
    return " ".join(args), default


async def _table_or_picker(
    update: Update, rt: Runtime, query: str, action: str, manual_only: bool = False
) -> TableConfig | None:
    tables = [t for t in rt.cfg.tables if t.is_manual or not manual_only]
    if query:
        table = find_table(rt.cfg, query)
        if table is not None:
            return table
        await _reply(update, f"Tavolo «{escape(query)}» non trovato o ambiguo. Scegli:",
                     table_picker(tables, action))
        return None
    if not tables:
        await _reply(update, "Nessun tavolo manuale configurato.")
        return None
    await _reply(update, "Scegli il tavolo:", table_picker(tables, action))
    return None


@authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP)


@authorized
async def cmd_tables(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, tables_text(_rt(context).tracker))


@authorized
async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    query, n = _split_count(context.args or [], DEFAULT_LAST, MAX_LAST)
    table = await _table_or_picker(update, rt, query, "u")
    if table:
        await _reply(update, last_text(table, rt.store.recent(table.id, n)))


@authorized
async def cmd_streaks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    query = " ".join(context.args or [])
    if not query:
        await _reply(update, all_streaks_text(rt.tracker))
        return
    table = await _table_or_picker(update, rt, query, "se")
    if table:
        await _reply(update, table_streaks_text(table, rt.tracker.recent[table.id]))


@authorized
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    query, n = _split_count(context.args or [], DEFAULT_STATS, MAX_STATS)
    table = await _table_or_picker(update, rt, query, "st")
    if table:
        await _reply(update, stats_text(table, rt.store.recent(table.id, n)))


@authorized
async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    args = context.args or []
    if not args:
        text, buttons = wizard(rt, update.effective_chat.id, "al:start")
        await _reply(update, text, buttons)
        return
    try:
        key, length, scope, target = parse_alert(rt.cfg, args)
    except ValueError as exc:
        await _reply(update, str(exc))
        return
    await _reply(update, create_alert(rt, update.effective_chat.id, key, length, scope, target))


@authorized
async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, buttons = alerts_view(_rt(context), update.effective_chat.id)
    await _reply(update, text, buttons)


@authorized
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    args = context.args or []
    if len(args) < 2:
        await _reply(update, "Uso: <code>/add tavolo 17 0 32</code> (dal più vecchio al più recente).")
        return
    table = find_table(rt.cfg, args[0])
    if table is None:
        await _reply(update, f"Tavolo «{escape(args[0])}» non trovato o ambiguo. Vedi /tavoli.")
        return
    try:
        numbers = parse_numbers(table, args[1:])
    except ValueError as exc:
        await _reply(update, f"Niente registrato: {escape(str(exc))}.")
        return
    text, notes = record(rt, table, numbers)
    await _reply(update, text)
    await deliver(context.bot, notes)


@authorized
async def cmd_insert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    table = await _table_or_picker(update, rt, " ".join(context.args or []), "in", manual_only=True)
    if table:
        await _reply(update, start_insert(context.chat_data, table))


@authorized
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data.pop("insert", None)
    await _reply(update, "Inserimento rapido terminato.")


@authorized
async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    query = " ".join(context.args or []) or context.chat_data.get("insert", "")
    table = await _table_or_picker(update, rt, query, "un", manual_only=True)
    if table:
        await _reply(update, undo(rt, table))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In inserimento rapido, i messaggi con soli numeri vengono registrati."""
    table_id = context.chat_data.get("insert")
    message = update.effective_message
    if not table_id or message is None or not message.text or not _is_allowed(update, context):
        return
    rt = _rt(context)
    table = rt.cfg.table(table_id)
    if table is None:
        context.chat_data.pop("insert", None)
        return
    try:
        numbers = parse_numbers(table, message.text.split())
    except ValueError as exc:
        await _reply(update, f"Niente registrato: {escape(str(exc))}. /fine per uscire.")
        return
    text, notes = record(rt, table, numbers)
    await _reply(update, text)
    await deliver(context.bot, notes)


def table_action(
    rt: Runtime, chat_data: MutableMapping[str, Any], action: str, table: TableConfig
) -> str:
    if action == "u":
        return last_text(table, rt.store.recent(table.id, DEFAULT_LAST))
    if action == "se":
        return table_streaks_text(table, rt.tracker.recent[table.id])
    if action == "st":
        return stats_text(table, rt.store.recent(table.id, DEFAULT_STATS))
    if action == "in":
        return start_insert(chat_data, table)
    if action == "un":
        return undo(rt, table)
    return "Azione sconosciuta."


async def _edit(query: Any, text: str, buttons: Buttons | None) -> None:
    try:
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=_markup(buttons),
            disable_web_page_preview=True,
        )
    except BadRequest as exc:  # es. "message is not modified" dopo un doppio click
        log.debug("modifica messaggio ignorata: %s", exc)


@authorized
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data or update.effective_chat is None:
        return
    rt = _rt(context)
    chat_id = update.effective_chat.id
    action, _, arg = query.data.partition(":")
    await query.answer()
    if action == "al":
        text, buttons = wizard(rt, chat_id, query.data)
        await _edit(query, text, buttons)
    elif action == "del":
        if arg.isdigit():
            rt.store.delete_alert(chat_id, int(arg))
        text, buttons = alerts_view(rt, chat_id)
        await _edit(query, text, buttons)
    else:
        table = rt.cfg.table(arg)
        if table is None:
            await _edit(query, "Tavolo non trovato.", None)
            return
        await _edit(query, table_action(rt, context.chat_data, action, table), None)


async def _simulator_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    sim = rt.simulators[context.job.data]
    _, notes = rt.tracker.add_spins(sim.table.id, [(sim.spin(), None)])
    await deliver(context.bot, notes)


async def _feed_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = _rt(context)
    await poll_feed(rt, rt.feeds[context.job.data], context.bot)


async def poll_feed(rt: Runtime, feed: HttpJsonSource, bot: Any) -> None:
    table = feed.table
    try:
        spins = await feed.fetch()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        feed.failures += 1
        if feed.failures == 1 or feed.failures % 20 == 0:
            log.warning("%s: feed non disponibile (%d errori di fila): %r", table.id, feed.failures, exc)
        return
    if feed.failures:
        log.info("%s: feed di nuovo disponibile", table.id)
        feed.failures = 0
    added, notes = rt.tracker.add_spins(table.id, spins, notify=feed.primed)
    if not feed.primed:
        log.info("%s: recuperati %d giri dallo storico del feed", table.id, added)
        feed.primed = True
    await deliver(bot, notes)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("errore nel gestore Telegram", exc_info=context.error)


async def _post_init(app: Application) -> None:
    cfg: RouletteConfig = app.bot_data["cfg"]
    rt = create_runtime(cfg, Store(cfg.database))
    app.bot_data["rt"] = rt
    jq = app.job_queue
    if jq is None:
        raise RuntimeError("installa python-telegram-bot[job-queue]")
    for table_id, sim in rt.simulators.items():
        interval = sim.table.interval_sec
        jq.run_repeating(_simulator_job, interval=interval, first=random.uniform(1, interval),
                         data=table_id, name=f"sim:{table_id}")
    for table_id, feed in rt.feeds.items():
        jq.run_repeating(_feed_job, interval=feed.table.interval_sec, first=random.uniform(1, 5),
                         data=table_id, name=f"feed:{table_id}")
    await app.bot.set_my_commands(COMMANDS)
    log.info("roulette tracker avviato: %d tavoli", len(cfg.tables))


async def _post_shutdown(app: Application) -> None:
    rt: Runtime | None = app.bot_data.get("rt")
    if rt is None:
        return
    if rt.http is not None:
        await rt.http.aclose()
    rt.store.close()


def build_application(cfg: RouletteConfig) -> Application:
    app = (
        ApplicationBuilder()
        .token(cfg.telegram_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["cfg"] = cfg
    for name, fn in [
        ("start", cmd_help),
        ("help", cmd_help),
        ("tavoli", cmd_tables),
        ("ultimi", cmd_last),
        ("serie", cmd_streaks),
        ("stats", cmd_stats),
        ("alert", cmd_alert),
        ("alerts", cmd_alerts),
        ("add", cmd_add),
        ("inserisci", cmd_insert),
        ("fine", cmd_done),
        ("annulla", cmd_undo),
    ]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(_on_error)
    return app
