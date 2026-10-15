from types import SimpleNamespace

import pytest

from roulette.bot import (
    authorized,
    build_application,
    create_runtime,
    find_table,
    on_button,
    on_text,
    parse_alert,
    parse_numbers,
    record,
    start_insert,
    undo,
    wizard,
)
from roulette.config import config_from_dict
from roulette.store import Store

TABLES = [
    {"id": "evo-lightning", "name": "Lightning Roulette", "provider": "Evolution"},
    {"id": "evo-xxx", "name": "XXXtreme Lightning Roulette", "provider": "Evolution"},
    {"id": "pp-mega", "name": "Mega Roulette", "provider": "Pragmatic Play"},
    {"id": "us", "name": "American", "provider": "Demo", "wheel": "american"},
    {"id": "sim", "name": "Simulata", "provider": "Demo", "source": "simulator"},
]
CHAT = 42


@pytest.fixture
def rt():
    cfg = config_from_dict({"telegram": {"allowed_users": [7]}, "tables": TABLES})
    return create_runtime(cfg, Store(), clock=lambda: 1_760_000_000.0)


def test_find_table(rt):
    assert find_table(rt.cfg, "pp-mega").id == "pp-mega"
    assert find_table(rt.cfg, "mega").id == "pp-mega"
    assert find_table(rt.cfg, "Lightning Roulette").id == "evo-lightning"  # nome esatto
    assert find_table(rt.cfg, "lightning") is None  # ambiguo
    assert find_table(rt.cfg, "xxx").id == "evo-xxx"


def test_parse_alert(rt):
    assert parse_alert(rt.cfg, ["rosso", "6"]) == ("rosso", 6, "all", "")
    assert parse_alert(rt.cfg, ["8", "neri", "evolution"]) == ("nero", 8, "provider", "evolution")
    assert parse_alert(rt.cfg, ["pari", "5", "Pragmatic", "Play"]) == (
        "pari", 5, "provider", "pragmatic-play"
    )
    assert parse_alert(rt.cfg, ["d1", "7", "mega"]) == ("d1", 7, "table", "pp-mega")
    for bad in (["rosso"], ["viola", "5"], ["rosso", "1"], ["rosso", "99"], ["rosso", "5", "boh"]):
        with pytest.raises(ValueError):
            parse_alert(rt.cfg, bad)


def test_wizard_creates_alert_step_by_step(rt):
    text, buttons = wizard(rt, CHAT, "al:start")
    flat = [d for row in buttons for _, d in row]
    assert "al:k:rosso" in flat and "al:k:c3" in flat
    assert all(len(d.encode()) <= 64 for d in flat)

    text, buttons = wizard(rt, CHAT, "al:k:nero")
    assert "Nero" in text
    assert "al:n:nero:6" in [d for row in buttons for _, d in row]

    text, buttons = wizard(rt, CHAT, "al:n:nero:6")
    flat = [d for row in buttons for _, d in row]
    assert {"al:s:nero:6:a", "al:s:nero:6:p:evolution", "al:t:nero:6"} <= set(flat)

    text, buttons = wizard(rt, CHAT, "al:t:nero:6")
    assert "al:s:nero:6:t:pp-mega" in [d for row in buttons for _, d in row]

    text, buttons = wizard(rt, CHAT, "al:s:nero:6:p:evolution")
    assert buttons is None and "creato" in text and "Evolution" in text
    (alert,) = rt.store.alerts(CHAT)
    assert (alert.category, alert.length, alert.scope, alert.target) == (
        "nero", 6, "provider", "evolution"
    )
    text, _ = wizard(rt, CHAT, "al:s:nero:6:p:evolution")
    assert "identico" in text
    for bad in ("al:k:viola", "al:n:nero:abc", "al:s:nero:6:t:inesistente", "al:s:nero:99:a"):
        text, buttons = wizard(rt, CHAT, bad)
        assert buttons is None and "creato" not in text


def test_alert_limit_per_chat(rt):
    rt.cfg.max_alerts_per_chat = 2
    for n in (3, 4):
        wizard(rt, CHAT, f"al:s:rosso:{n}:a")
    text, _ = wizard(rt, CHAT, "al:s:rosso:5:a")
    assert "Hai già 2 alert" in text


def test_manual_record_and_undo(rt):
    table = rt.cfg.table("evo-lightning")
    rt.store.add_alert(CHAT, "rosso", 3, "all", "", 0)
    text, notes = record(rt, table, parse_numbers(table, ["1,3", "5"]))
    assert "🔴1" in text and len(notes) == 1 and notes[0].chat_id == CHAT
    assert "cancellato 🔴5" in undo(rt, table)
    assert list(rt.tracker.recent["evo-lightning"]) == [1, 3]
    with pytest.raises(ValueError):
        parse_numbers(table, ["17", "00"])  # 00 solo sull'americana
    assert parse_numbers(rt.cfg.table("us"), ["00"]) == [37]
    text, notes = record(rt, rt.cfg.table("sim"), [5])
    assert "automatico" in text and notes == []
    assert "automatico" in start_insert({}, rt.cfg.table("sim"))


class Msg:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class Query:
    def __init__(self, data):
        self.data = data
        self.edits = []

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, **kw):
        self.edits.append((text, kw.get("reply_markup")))


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


def _context(rt, chat_data=None, args=None):
    return SimpleNamespace(
        application=SimpleNamespace(bot_data={"cfg": rt.cfg, "rt": rt}),
        chat_data=chat_data if chat_data is not None else {},
        args=args or [],
        bot=Bot(),
    )


def _update(user_id, text="", data=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=CHAT),
        effective_message=Msg(text),
        callback_query=Query(data) if data else None,
    )


async def test_unauthorized_user_is_rejected(rt):
    called = []

    @authorized
    async def handler(update, context):
        called.append(update.effective_user.id)

    upd = _update(999)
    await handler(upd, _context(rt))
    assert called == [] and "999" in upd.effective_message.replies[0]
    await handler(_update(7), _context(rt))
    assert called == [7]


async def test_quick_insert_mode(rt):
    chat_data = {}
    start_insert(chat_data, rt.cfg.table("pp-mega"))
    rt.store.add_alert(CHAT, "verde", 2, "table", "pp-mega", 0)
    ctx = _context(rt, chat_data)
    upd = _update(7, "0 0")
    await on_text(upd, ctx)
    assert rt.store.recent("pp-mega", 5) == [0, 0]
    assert "🟢0" in upd.effective_message.replies[0]
    assert ctx.bot.sent and "2 di fila" in ctx.bot.sent[0][1]

    upd = _update(7, "ciao")
    await on_text(upd, ctx)
    assert "Niente registrato" in upd.effective_message.replies[0]
    assert rt.store.recent("pp-mega", 5) == [0, 0]

    intruder = _update(999, "17")
    await on_text(intruder, ctx)
    assert intruder.effective_message.replies == [] and rt.store.count("pp-mega") == 2


async def test_buttons_delete_alert_and_show_table(rt):
    alert_id = rt.store.add_alert(CHAT, "rosso", 5, "all", "", 0)
    upd = _update(7, data=f"del:{alert_id}")
    await on_button(upd, _context(rt))
    assert rt.store.alerts(CHAT) == []
    assert "Nessun alert" in upd.callback_query.edits[0][0]

    rt.tracker.add_spins("pp-mega", [(17, None)])
    upd = _update(7, data="u:pp-mega")
    await on_button(upd, _context(rt))
    assert "⚫17" in upd.callback_query.edits[0][0]


def test_application_builds_without_network(rt):
    cfg = config_from_dict(
        {"telegram": {"token": "123:abc", "allowed_users": [1]}, "tables": TABLES}
    )
    app = build_application(cfg)
    commands = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
    assert {"tavoli", "ultimi", "serie", "stats", "alert", "alerts", "add", "inserisci"} <= commands


def test_rejected_token_gives_a_clear_message(tmp_path, monkeypatch, capsys):
    from telegram.error import InvalidToken

    import roulette.bot
    from roulette.__main__ import main

    class App:
        def run_polling(self, **kw):
            raise InvalidToken("rejected")

    monkeypatch.setattr(roulette.bot, "build_application", lambda cfg: App())
    config = tmp_path / "config.toml"
    config.write_text(
        '[telegram]\ntoken = "1:abc"\nallowed_users = [1]\n'
        '[[tables]]\nid = "t"\nname = "T"\nprovider = "P"\n',
        encoding="utf-8",
    )
    assert main(["--config", str(config)]) == 2
    assert "rifiutato il token" in capsys.readouterr().err
