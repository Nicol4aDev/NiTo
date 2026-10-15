import json
import random

import httpx
import pytest

from roulette.bot import create_runtime, poll_feed
from roulette.config import config_from_dict
from roulette.formatting import all_streaks_text, stats_text, table_streaks_text, tables_text
from roulette.sources import HttpJsonSource, Simulator, get_path, parse_feed
from roulette.store import Store
from roulette.tracker import Tracker

TABLES = [
    {"id": "evo-light", "name": "Lightning Roulette", "provider": "Evolution"},
    {"id": "evo-imm", "name": "Immersive Roulette", "provider": "Evolution"},
    {"id": "pp-mega", "name": "Mega Roulette", "provider": "Pragmatic Play"},
]

RED_SEQ = [1, 3, 5, 7, 9, 12, 14, 16]
CHAT, OTHER = 100, 200


def make(general=None, tables=TABLES):
    cfg = config_from_dict({"general": general or {}, "tables": tables})
    store = Store()
    return Tracker(cfg, store, clock=lambda: 1_760_000_000.0), store


def spins(numbers):
    return [(n, None) for n in numbers]


def test_alert_fires_at_threshold_then_continues_then_breaks():
    tracker, store = make()
    store.add_alert(CHAT, "rosso", 4, "all", "", 0)
    _, notes = tracker.add_spins("evo-light", spins(RED_SEQ[:3]))
    assert notes == []
    _, notes = tracker.add_spins("evo-light", spins([RED_SEQ[3]]))
    assert len(notes) == 1 and notes[0].chat_id == CHAT
    assert "4 di fila" in notes[0].text and "Lightning Roulette" in notes[0].text
    _, notes = tracker.add_spins("evo-light", spins([RED_SEQ[4]]))
    assert "continua" in notes[0].text and "5 di fila" in notes[0].text
    _, notes = tracker.add_spins("evo-light", spins([0]))
    assert "interrotta dopo 5" in notes[0].text and "🟢0" in notes[0].text
    _, notes = tracker.add_spins("evo-light", spins([2]))
    assert notes == []


def test_batch_compares_state_before_and_after():
    tracker, store = make()
    store.add_alert(CHAT, "rosso", 3, "all", "", 0)
    # la serie arriva a 4 e si interrompe dentro la stessa sequenza: niente di attuale
    _, notes = tracker.add_spins("evo-light", spins(RED_SEQ[:4] + [2, 4]))
    assert notes == []
    # la soglia viene superata dentro la sequenza: è comunque un nuovo alert
    _, notes = tracker.add_spins("evo-light", spins(RED_SEQ[:5]))
    assert len(notes) == 1 and "🚨 🔴 Rosso: <b>5 di fila" in notes[0].text
    # rosso, nero, poi tre rossi: interruzione dopo 6 e nuova serie da 3
    _, notes = tracker.add_spins("evo-light", spins([5, 2, 1, 3, 5]))
    text = notes[0].text
    assert "interrotta dopo 6" in text and "🚨 🔴 Rosso: <b>3 di fila" in text
    assert "Uscito: 🔴5 ⚫2 🔴1 🔴3 🔴5" in text
    _, notes = tracker.add_spins("evo-light", spins([2, 4]))
    assert "interrotta dopo 3" in notes[0].text and "di fila" not in notes[0].text


def test_continuation_and_break_can_be_disabled():
    tracker, store = make({"notify_continuation": False, "notify_break": False})
    store.add_alert(CHAT, "rosso", 3, "all", "", 0)
    store.add_alert(CHAT, "rosso", 6, "all", "", 0)
    texts = []
    for n in RED_SEQ[:7] + [2]:
        _, notes = tracker.add_spins("pp-mega", spins([n]))
        texts.append(notes[0].text if notes else None)
    assert [i + 1 for i, t in enumerate(texts) if t] == [3, 6]  # solo alle soglie


def test_scope_by_provider_and_table():
    tracker, store = make()
    store.add_alert(CHAT, "nero", 2, "provider", "evolution", 0)
    store.add_alert(OTHER, "nero", 2, "table", "pp-mega", 0)
    _, notes = tracker.add_spins("evo-imm", spins([2, 4]))
    assert [n.chat_id for n in notes] == [CHAT]
    _, notes = tracker.add_spins("pp-mega", spins([2, 4]))
    assert [n.chat_id for n in notes] == [OTHER]


def test_overlapping_alerts_produce_one_message_per_chat():
    tracker, store = make()
    store.add_alert(CHAT, "rosso", 3, "all", "", 0)
    store.add_alert(CHAT, "rosso", 3, "provider", "evolution", 0)
    store.add_alert(CHAT, "dispari", 3, "all", "", 0)
    _, notes = tracker.add_spins("evo-light", spins([1, 3, 5]))
    assert len(notes) == 1
    assert "Rosso" in notes[0].text and "Dispari" in notes[0].text


def test_duplicates_ignored_and_backfill_is_silent():
    tracker, store = make()
    store.add_alert(CHAT, "rosso", 2, "all", "", 0)
    added, notes = tracker.add_spins("evo-light", [(1, "a"), (3, "b"), (5, "c")], notify=False)
    assert added == 3 and notes == []
    added, notes = tracker.add_spins("evo-light", [(3, "b"), (5, "c"), (7, "d")])
    assert added == 1
    assert "4 di fila" in notes[0].text
    assert store.count("evo-light") == 4


def test_history_survives_restart_and_undo():
    tracker, store = make()
    tracker.add_spins("evo-light", spins([1, 3, 2]))
    again = Tracker(tracker.cfg, store)
    assert list(again.recent["evo-light"]) == [1, 3, 2]
    assert again.undo("evo-light") == 2
    assert list(again.recent["evo-light"]) == [1, 3]
    assert store.recent("evo-light", 10) == [1, 3]
    assert again.streaks("evo-light")["rosso"] == 2


def test_store_alerts_are_per_chat():
    _, store = make()
    a = store.add_alert(CHAT, "rosso", 5, "all", "", 0)
    assert store.add_alert(CHAT, "rosso", 5, "all", "", 0) is None  # duplicato
    assert store.add_alert(OTHER, "rosso", 5, "all", "", 0) is not None
    assert not store.delete_alert(OTHER, a)  # non è suo
    assert store.delete_alert(CHAT, a)
    assert [x.chat_id for x in store.alerts()] == [OTHER]


def test_texts_render():
    tracker, _ = make()
    tracker.add_spins("evo-light", spins(RED_SEQ[:5] + [0, 2]))
    tracker.add_spins("pp-mega", spins(RED_SEQ[:4]))
    overview = tables_text(tracker)
    assert "Evolution" in overview and "Pragmatic Play" in overview and "🔴 Rosso ×4" in overview
    assert "nessun dato" in overview  # evo-imm vuoto
    tracker.add_spins("evo-imm", spins(RED_SEQ[:3]))
    streaks = all_streaks_text(tracker, 3)
    assert streaks.index("Mega Roulette") < streaks.index("Immersive")  # ordinati per lunghezza
    assert "Lightning" not in streaks  # nessuna serie di almeno 3
    table = tracker.tables["evo-light"]
    detail = table_streaks_text(table, tracker.recent["evo-light"])
    assert "🟢 Verde 1" in detail  # ritardo dello zero
    stats = stats_text(table, list(tracker.recent["evo-light"]))
    assert "🔴 Rosso 5 (71%)" in stats and "🟢 Verde 1 (14%)" in stats
    assert "Serie più lunghe" in stats and "🔴 Rosso 5" in stats
    # caldi: solo numeri usciti; freddi: mai usciti, a partire dal più basso
    assert "🔥 Caldi: 2 (1) · 0 (1) · 9 (1)" in stats
    assert "🧊 Freddi: 4 (0) · 6 (0)" in stats


def test_get_path_and_parse_feed():
    payload = {"data": {"results": [
        {"gameId": "c", "result": {"number": "00"}},
        {"gameId": "b", "result": {"number": 17}},
        {"gameId": "x", "result": {}},  # senza numero: ignorato
        {"gameId": "a", "result": {"number": 0}},
    ]}}
    assert get_path(payload, "data.results.1.gameId") == "b"
    with pytest.raises(KeyError):
        get_path(payload, "data.risultati")
    cfg = config_from_dict({"tables": [{
        "id": "f", "name": "F", "provider": "P", "source": "http_json", "url": "https://x",
        "items_path": "data.results", "number_path": "result.number", "id_path": "gameId",
    }]})
    table = cfg.tables[0]
    # "00" non esiste sulla ruota europea: scartato
    assert parse_feed(table, payload) == [(0, "a"), (17, "b")]
    single = {"gameId": "z", "result": {"number": 5}}
    table.items_path = ""
    assert parse_feed(table, single) == [(5, "z")]


async def test_poll_feed_backfills_silently_then_alerts():
    feed_items = [{"id": str(i), "n": n} for i, n in enumerate([1, 3, 5])]

    def handler(request):
        return httpx.Response(200, text=json.dumps(list(reversed(feed_items))))

    cfg = config_from_dict({"tables": [{
        "id": "f", "name": "Feed", "provider": "P", "source": "http_json", "url": "https://x",
        "number_path": "n", "id_path": "id",
    }]})
    rt = create_runtime(cfg, Store())
    await rt.http.aclose()
    rt.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    feed = HttpJsonSource(cfg.tables[0], rt.http)
    rt.store.add_alert(CHAT, "rosso", 3, "all", "", 0)

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append((chat_id, text))

    bot = Bot()
    await poll_feed(rt, feed, bot)
    assert feed.primed and bot.sent == [] and list(rt.tracker.recent["f"]) == [1, 3, 5]
    feed_items.append({"id": "3", "n": 7})
    await poll_feed(rt, feed, bot)
    assert len(bot.sent) == 1 and "4 di fila" in bot.sent[0][1]

    def broken(request):
        return httpx.Response(500)

    feed.client = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    await poll_feed(rt, feed, bot)
    assert feed.failures == 1
    await rt.http.aclose()
    await feed.client.aclose()


def test_simulator_respects_wheel():
    cfg = config_from_dict({"tables": [
        {"id": "s", "name": "S", "provider": "P", "source": "simulator", "wheel": "american"},
    ]})
    sim = Simulator(cfg.tables[0], random.Random(1))
    seen = {sim.spin() for _ in range(2000)}
    assert seen == set(range(38))
