"""Avvio del bot: `python -m roulette` (legge config.toml)."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from .config import ConfigError, load_config, validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="roulette", description="Bot Telegram che traccia i risultati delle roulette"
    )
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--check", action="store_true", help="valida la configurazione ed esci")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        cfg = load_config(args.config)
        validate(cfg, require_telegram=True)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.check:
        sources = Counter(t.source for t in cfg.tables)
        detail = ", ".join(f"{n} {s}" for s, n in sources.items())
        print(f"Configurazione valida: {len(cfg.tables)} tavoli ({detail}), "
              f"{len(cfg.providers())} provider.")
        return 0

    from telegram.error import InvalidToken

    from .bot import build_application

    try:
        build_application(cfg).run_polling(allowed_updates=["message", "callback_query"])
    except InvalidToken:
        print(
            "Telegram ha rifiutato il token. Copia di nuovo il token da @BotFather "
            "(/mybots → il tuo bot → API Token) e incollalo in config.toml, voce token.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
