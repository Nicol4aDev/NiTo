"""Interfaccia Telegram: comandi, conferme con pulsanti e job periodici."""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .brokers import LiveBroker, PaperBroker
from .config import Config, ConfigError, Mode
from .engine import Buttons, Engine
from .market_data import MarketData, create_exchange, timeframe_ms
from .storage import Store

log = logging.getLogger(__name__)

HELP = """<b>Comandi NiTo</b>
/status — stato, capitale, blocchi di sicurezza
/positions — posizioni aperte con TP/SL
/stats — statistiche delle operazioni chiuse
/scan — analizza subito i mercati
/config — parametri di rischio attivi
/pause — sospende i nuovi segnali (le posizioni restano gestite)
/resume — riattiva i segnali e azzera il kill switch
/panic — chiude TUTTE le posizioni e blocca il bot
/help — questo messaggio"""

COMMANDS = [
    BotCommand("status", "Stato e capitale"),
    BotCommand("positions", "Posizioni aperte"),
    BotCommand("stats", "Statistiche"),
    BotCommand("scan", "Analizza subito i mercati"),
    BotCommand("config", "Parametri di rischio"),
    BotCommand("pause", "Sospendi i nuovi segnali"),
    BotCommand("resume", "Riattiva i segnali"),
    BotCommand("panic", "Chiudi tutto e blocca"),
    BotCommand("help", "Aiuto"),
]


class TelegramNotifier:
    def __init__(self, bot: Any, chat_id: str):
        self.bot = bot
        self.chat_id = chat_id

    async def send(self, text: str, buttons: Buttons | None = None) -> None:
        markup = None
        if buttons:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in buttons]
            )
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except TelegramError:
            log.exception("invio messaggio Telegram fallito")


Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def authorized(func: Handler) -> Handler:
    """Solo gli user id in TELEGRAM_ALLOWED_USERS possono usare i comandi."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cfg: Config = context.application.bot_data["cfg"]
        user = update.effective_user
        if user is None or user.id not in cfg.secrets.allowed_user_ids:
            uid = user.id if user else "?"
            log.warning("accesso negato all'utente %s", uid)
            if update.callback_query:
                await update.callback_query.answer("⛔ Non autorizzato", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(
                    f"⛔ Non autorizzato. Il tuo user id è {uid}: aggiungilo a "
                    "TELEGRAM_ALLOWED_USERS se questo è il tuo bot."
                )
            return
        await func(update, context)

    return wrapper


def _engine(context: ContextTypes.DEFAULT_TYPE) -> Engine:
    return context.application.bot_data["engine"]


async def _reply(update: Update, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True
        )


@authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP)


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, await _engine(context).status_text())


@authorized
async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, await _engine(context).positions_text())


@authorized
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, _engine(context).stats_text())


@authorized
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    engine = _engine(context)
    if engine.guard.paused:
        await _reply(update, "⏸ Il bot è in pausa: usa /resume.")
        return
    await _reply(update, "🔎 Analisi in corso…")
    sent = await engine.scan()
    await _reply(update, f"Analisi completata: {len(sent)} nuovi segnali.")


@authorized
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    r, s, ex = cfg.risk, cfg.strategy, cfg.execution
    text = "\n".join(
        [
            "<b>Configurazione</b>",
            f"Modalità: {cfg.mode.value} · conferma manuale: {'sì' if ex.require_confirmation else 'no'}",
            f"Setup attivi: {', '.join(s.enabled)}",
            f"Filtro trend: {'sì' if s.trend_filter else 'no'} · short: {'sì' if s.allow_short else 'no'}",
            f"Rischio/trade: {r.risk_per_trade_pct}% · R:R {r.reward_risk} (min netto {r.min_net_reward_risk})",
            f"SL: {r.sl_mode} · min {r.min_sl_pct}% · max {r.max_sl_pct}%",
            f"Break-even a +{r.break_even_at_r}R",
            f"Max posizioni: {r.max_open_positions} · leva max: {r.max_leverage}x",
            f"Stop giornaliero: -{r.max_daily_loss_pct}% · pausa dopo {r.max_consecutive_losses} perdite",
        ]
    )
    await _reply(update, text)


@authorized
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _engine(context).guard.set_paused(True)
    await _reply(update, "⏸ Nuovi segnali sospesi. Le posizioni aperte restano gestite con TP/SL.")


@authorized
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    guard = _engine(context).guard
    guard.set_paused(False)
    guard.resume()
    msg = "▶️ Segnali riattivati."
    if guard.daily_halt:
        msg += f"\n🛑 Resta attivo il blocco giornaliero: {guard.daily_halt}"
    await _reply(update, msg)


@authorized
async def cmd_panic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    markup = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🧯 Sì, chiudi tutto", callback_data="panic:yes"),
            InlineKeyboardButton("Annulla", callback_data="panic:no"),
        ]]
    )
    await _reply(update, "Chiudere TUTTE le posizioni a mercato e bloccare il bot?", markup)


@authorized
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    engine = _engine(context)
    action, _, arg = query.data.partition(":")
    await query.answer()
    if action == "panic":
        await query.edit_message_reply_markup(None)
        if arg != "yes":
            await _reply(update, "Annullato.")
            return
        results = await engine.panic()
        await _reply(update, "🛑 Kill switch attivo.\n\n" + ("\n\n".join(results) or "Nessuna posizione aperta."))
        return
    if action in ("exec", "skip"):
        try:
            signal_id = int(arg)
        except ValueError:
            return
        await query.edit_message_reply_markup(None)
        if action == "exec":
            result = await engine.confirm(signal_id)
        else:
            result = engine.skip(signal_id)
        await _reply(update, result)


async def _scan_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _engine(context).scan()


async def _monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _engine(context).monitor()


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("errore nel gestore Telegram", exc_info=context.error)


async def _post_init(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    exchange = create_exchange(cfg)
    app.bot_data["exchange"] = exchange
    await exchange.load_markets()
    missing = [s for s in cfg.market.symbols + [cfg.market.btc_symbol] if s not in exchange.markets]
    if missing:
        raise ConfigError(f"simboli non trovati su {cfg.market.exchange}: {', '.join(missing)}")

    store = Store(cfg.database, mode=cfg.mode.value)
    data = MarketData(exchange)
    if cfg.mode is Mode.LIVE:
        broker = LiveBroker(exchange, cfg)
    else:
        broker = PaperBroker(store, cfg.risk.account_equity, cfg.risk.fee_pct, data.last_price)
    notifier = TelegramNotifier(app.bot, cfg.secrets.telegram_chat_id)
    engine = Engine(cfg, data, broker, store, notifier)
    app.bot_data["engine"] = engine
    app.bot_data["store"] = store

    await app.bot.set_my_commands(COMMANDS)
    await engine.startup()

    tf_s = timeframe_ms(cfg.market.timeframe) / 1000
    now = time.time()
    first = tf_s - (now % tf_s) + 3  # 3 secondi dopo la chiusura della candela
    jq = app.job_queue
    if jq is None:
        raise RuntimeError("installa python-telegram-bot[job-queue]")
    jq.run_repeating(_scan_job, interval=tf_s, first=first, name="scan")
    jq.run_repeating(_monitor_job, interval=cfg.execution.monitor_interval_sec, first=5, name="monitor")


async def _post_shutdown(app: Application) -> None:
    exchange = app.bot_data.get("exchange")
    if exchange is not None:
        await exchange.close()
    store = app.bot_data.get("store")
    if store is not None:
        store.close()


def build_application(cfg: Config) -> Application:
    app = (
        ApplicationBuilder()
        .token(cfg.secrets.telegram_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["cfg"] = cfg
    for name, fn in [
        ("start", cmd_help),
        ("help", cmd_help),
        ("status", cmd_status),
        ("positions", cmd_positions),
        ("stats", cmd_stats),
        ("scan", cmd_scan),
        ("config", cmd_config),
        ("pause", cmd_pause),
        ("resume", cmd_resume),
        ("panic", cmd_panic),
    ]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(_on_error)
    return app
