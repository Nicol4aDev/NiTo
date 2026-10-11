"""Avvio del bot: `python -m nito --config config.toml`."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError, load_config, validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nito", description="Bot Telegram di segnali/trading")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--env", default=".env", help="file con i segreti (default .env)")
    parser.add_argument("--check", action="store_true", help="valida la configurazione ed esci")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        from dotenv import load_dotenv

        load_dotenv(args.env)
    except ImportError:
        pass

    try:
        cfg = load_config(args.config)
        validate(cfg, require_telegram=True)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.check:
        print(f"Configurazione valida: modalità {cfg.mode.value}, {len(cfg.market.symbols)} simboli.")
        return 0

    from .telegram_bot import build_application

    build_application(cfg).run_polling(allowed_updates=["message", "callback_query"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
