#!/usr/bin/env python3
"""Avvio guidato del roulette tracker: doppio clic su Windows, `python3 avvia_roulette.py` su Mac/Linux.

Al primo avvio crea un ambiente Python nella cartella `.venv`, installa le
librerie, crea `roulette.toml` dall'esempio e chiede token del bot e user id
Telegram (salvati in `.env`). Poi avvia il bot. Gli argomenti extra passano al
bot, es. `avvia_roulette.py --check`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PACKAGES = ["python-telegram-bot[job-queue]>=21.0,<23", "python-dotenv>=1.0", "httpx>=0.27"]
TOKEN_RE = re.compile(r"\d+:[A-Za-z0-9_-]{20,}")
IDS_RE = re.compile(r"\d+(\s*,\s*\d+)*")


def pause() -> None:
    if os.name == "nt":  # con il doppio clic la finestra si chiuderebbe subito
        input("\nPremi Invio per chiudere...")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_packages() -> None:
    if not venv_python().exists():
        print("Preparo l'ambiente Python (solo la prima volta)...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    probe = [str(venv_python()), "-c", "import telegram, dotenv, httpx, apscheduler"]
    if subprocess.run(probe, capture_output=True, check=False).returncode != 0:
        print("Installo le librerie (1-2 minuti, solo la prima volta)...")
        pip = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q"]
        subprocess.run([*pip, *PACKAGES], check=True)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    return values


def ask(prompt: str, pattern: re.Pattern[str], error: str) -> str:
    while True:
        value = input(prompt).strip()
        if pattern.fullmatch(value):
            return value
        print(error)


def ensure_settings() -> None:
    config = ROOT / "roulette.toml"
    if not config.exists():
        shutil.copy(ROOT / "roulette.example.toml", config)
        print("Creato roulette.toml con i tavoli di esempio.")

    env_path = ROOT / ".env"
    env = read_env(env_path)
    new: list[str] = []
    if not env.get("ROULETTE_BOT_TOKEN"):
        token = ask(
            "\nIncolla il token del bot (da @BotFather) e premi Invio:\n> ",
            TOKEN_RE,
            "Non sembra un token: deve essere tipo 123456789:AAH... Riprova.",
        )
        new.append(f"ROULETTE_BOT_TOKEN={token}")
    if not env.get("ROULETTE_ALLOWED_USERS"):
        ids = ask(
            "\nScrivi il tuo user id Telegram (da @userinfobot) e premi Invio:\n> ",
            IDS_RE,
            "Deve essere un numero (più id separati da virgola). Riprova.",
        )
        new.append(f"ROULETTE_ALLOWED_USERS={ids.replace(' ', '')}")
    if new:
        text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        env_path.write_text(text + "\n".join(new) + "\n", encoding="utf-8")
        print("Salvato nel file .env (per cambiarli, modifica o cancella quel file).")


def main() -> int:
    os.chdir(ROOT)
    if sys.version_info < (3, 11):  # noqa: UP036 - può partire con un Python vecchio
        print("ERRORE: serve Python 3.11 o superiore. Scaricalo da https://www.python.org/downloads/")
        pause()
        return 1
    try:
        ensure_packages()
        ensure_settings()
        print("\nAvvio il bot. Lascia aperta questa finestra; per fermarlo premi Ctrl+C.\n")
        cmd = [str(venv_python()), "-m", "nito.roulette", "--config", "roulette.toml", *sys.argv[1:]]
        code = subprocess.run(cmd, check=False).returncode
    except KeyboardInterrupt:
        print("\nBot fermato.")
        return 0
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"\nERRORE: {exc}")
        code = 1
    if code != 0:
        print("\nIl bot si è fermato con un errore: leggi i messaggi qui sopra.")
        pause()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
