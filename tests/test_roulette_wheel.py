import pytest

from nito.config import ConfigError
from nito.roulette.config import config_from_dict, load_config, validate
from nito.roulette.wheel import (
    DOUBLE_ZERO,
    RED,
    Wheel,
    categories_of,
    current_streak,
    find_category,
    fmt_pocket,
    gap,
    longest_streak,
    parse_pocket,
)


def test_colors():
    assert len(RED) == 18
    assert categories_of(0) == {"verde"}
    assert categories_of(DOUBLE_ZERO) == {"verde"}
    assert categories_of(1) == {"rosso", "dispari", "basso", "d1", "c1"}
    assert categories_of(36) == {"rosso", "pari", "alto", "d3", "c3"}
    assert categories_of(11) == {"nero", "dispari", "basso", "d1", "c2"}
    assert categories_of(24) == {"nero", "pari", "alto", "d2", "c3"}
    assert fmt_pocket(32) == "🔴32" and fmt_pocket(15) == "⚫15" and fmt_pocket(DOUBLE_ZERO) == "🟢00"


def test_every_number_has_one_color_and_dozen_and_column():
    for n in range(1, 37):
        cats = categories_of(n)
        assert len(cats & {"rosso", "nero"}) == 1
        assert len(cats & {"d1", "d2", "d3"}) == 1
        assert len(cats & {"c1", "c2", "c3"}) == 1


def test_parse_pocket():
    assert parse_pocket("17", Wheel.EUROPEAN) == 17
    assert parse_pocket(" 0 ", Wheel.EUROPEAN) == 0
    assert parse_pocket("00", Wheel.AMERICAN) == DOUBLE_ZERO
    for bad in ("00", "37", "-1", "rosso", "", "1.5"):
        with pytest.raises(ValueError):
            parse_pocket(bad, Wheel.EUROPEAN)
    assert DOUBLE_ZERO in Wheel.AMERICAN.pockets and DOUBLE_ZERO not in Wheel.EUROPEAN.pockets


def test_find_category_aliases():
    assert find_category("Rossi").key == "rosso"
    assert find_category("black").key == "nero"
    assert find_category("zero").key == "verde"
    assert find_category("19-36").key == "alto"
    assert find_category("⚫️").key == "nero"  # con variation selector
    assert find_category("viola") is None


def test_streaks_zero_breaks_everything():
    seq = [3, 32, 19, 21]  # quattro rossi
    assert current_streak(seq, "rosso") == 4
    assert current_streak(seq + [0], "rosso") == 0
    assert current_streak(seq + [0], "verde") == 1
    assert current_streak([2, 4, 0, 6], "pari") == 1
    assert longest_streak([1, 3, 2, 5, 7, 9, 0], "dispari") == 3
    assert gap([0, 1, 2, 3], "verde") == 3
    assert gap([1, 2], "verde") is None
    assert gap([1, 2, 0], "verde") == 0


def test_example_config_is_valid():
    cfg = load_config("roulette.example.toml", env={})
    assert {t.source for t in cfg.tables} == {"manual", "simulator"}
    assert len(cfg.providers()) >= 4
    with pytest.raises(ConfigError):
        validate(cfg, require_telegram=True)
    env = {"ROULETTE_BOT_TOKEN": "1:a", "ROULETTE_ALLOWED_USERS": "5, 6"}
    cfg = load_config("roulette.example.toml", env=env)
    validate(cfg, require_telegram=True)
    assert cfg.allowed_user_ids == {5, 6}


def _table(**kw):
    return {"id": "t1", "name": "T", "provider": "P", **kw}


@pytest.mark.parametrize(
    "tables",
    [
        [],
        [_table(id="Con Spazi")],
        [_table(), _table()],
        [_table(source="websocket")],
        [_table(wheel="francese")],
        [_table(source="http_json", url="https://x", id_path="id")],  # manca number_path
        [_table(source="http_json", url="https://x", number_path="n")],  # manca id_path
        [_table(source="http_json", url="ftp://x", number_path="n", id_path="id")],
        [_table(colore="rosso")],
        [{"id": "t1", "name": "T"}],
    ],
)
def test_invalid_tables(tables):
    with pytest.raises(ConfigError):
        config_from_dict({"tables": tables}, env={})


def test_env_vars_in_feed_headers(monkeypatch):
    monkeypatch.setenv("TOKEN_TEST_ROULETTE", "segreto")
    t = _table(source="http_json", url="https://x/feed", number_path="n", id_path="id",
               headers={"Authorization": "Bearer ${TOKEN_TEST_ROULETTE}"})
    cfg = config_from_dict({"tables": [t]}, env={})
    assert cfg.tables[0].headers["Authorization"] == "Bearer segreto"
    assert cfg.tables[0].interval_sec == 15
