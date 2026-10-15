# Immagine del roulette tracker (python -m nito.roulette), pronta per servizi
# cloud come Railway. Segreti come variabili d'ambiente: ROULETTE_BOT_TOKEN,
# ROULETTE_ALLOWED_USERS e, con un disco persistente, ROULETTE_DATABASE.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN pip install "python-telegram-bot[job-queue]>=21.0,<23" "python-dotenv>=1.0" "httpx>=0.27"

COPY nito ./nito
COPY roulette*.toml ./
# senza un roulette.toml nel repository si usano i tavoli dell'esempio
RUN [ -f roulette.toml ] || cp roulette.example.toml roulette.toml

CMD ["python", "-m", "nito.roulette", "--config", "roulette.toml"]
