# NiTo — bot Telegram di segnali e trading con TP/SL

Bot Telegram che analizza i mercati crypto con i setup del metodo
**Scalping The Bull** (EMA 5/10/60/223, trigger point giornalieri) e invia
segnali con **entrata, Take Profit e Stop Loss** già calcolati. Può anche
eseguirli in automatico, con una serie di protezioni pensate per limitare le
perdite.

> ⚠️ **Avvertenza.** Nessun bot rende il trading "sicuro" nel senso di "senza
> perdite". Lo scalping con leva è ad alto rischio e la maggior parte dei
> trader retail perde denaro. "Sicuro" qui significa: rischio per trade
> limitato e noto, stop loss sempre presente sull'exchange, blocchi automatici
> e nessun ordine reale senza un consenso esplicito. Usa solo denaro che puoi
> permetterti di perdere. Se pubblichi i segnali ad altre persone, informati
> sugli obblighi legali in materia di consulenza finanziaria.

Le regole dei setup e le fonti sono in [docs/STRATEGIA.md](docs/STRATEGIA.md).

## Roulette tracker

Nel progetto c'è anche un secondo bot, indipendente: registra i numeri usciti
su più tavoli di roulette e provider, mostra statistiche e serie in corso e
avvisa quando un colore (o pari/dispari, dozzina, …) esce N volte di fila, con
N scelto da te. Si avvia con un doppio clic su `avvia_roulette.py` (oppure
`python -m nito.roulette`); guida completa in [docs/ROULETTE.md](docs/ROULETTE.md).

## Esempio di segnale

Valori illustrativi:

```
🟢 LONG SOLUSDT · 5m · #12
Setup: Viagra + Fake EMA10
  • 6 chiusure sotto la EMA10, poi rottura
  • falsa rottura della EMA10 riassorbita
Trend: 🟢 rialzista (EMA60 > EMA223)

▶️ Entrata: 145.3200
🎯 TP: 147.0800 (+1.21%)
🛑 SL: 144.4400 (-0.61%)
⚖️ R:R 2.00 (netto commissioni 1.50)
🔒 Stop a break-even quando tocca 146.2000

💰 Size: 9.48421 SOL (rischio 10.00 = 1% di 1000.00)
⏱ 11/10/2026 14:35 · modalità SIGNALS
```

## Modalità

| Modalità | Cosa fa | Chiavi API |
|---|---|---|
| `signals` (default) | invia i segnali e segue virtualmente l'esito (TP, SL, break-even) | no |
| `paper` | simulazione con capitale virtuale; ogni trade si conferma con un pulsante | no |
| `live` | ordini reali (testnet di default) | sì |

Percorso consigliato: **backtest → signals → paper → live su testnet → live
con capitale piccolo**. Non saltare passaggi.

## Installazione

Serve Python 3.11 o superiore.

```bash
git clone https://github.com/Nicol4aDev/NiTo.git
cd NiTo
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp config.example.toml config.toml
cp .env.example .env
```

### Creare il bot Telegram

1. Su Telegram scrivi a [@BotFather](https://t.me/BotFather), comando `/newbot`,
   e copia il token in `TELEGRAM_BOT_TOKEN`.
2. Avvia il bot e scrivigli `/start`: se non sei autorizzato ti risponde con il
   tuo **user id**. Mettilo in `TELEGRAM_ALLOWED_USERS` e in `TELEGRAM_CHAT_ID`
   (o usa l'id di un gruppo/canale dove il bot è amministratore).
3. Controlla la configurazione e avvia:

```bash
python -m nito --check
python -m nito
```

## Comandi

| Comando | Azione |
|---|---|
| `/status` | modalità, capitale, P&L di oggi, blocchi attivi |
| `/positions` | posizioni aperte con TP/SL e R attuale |
| `/stats` | win rate, P&L, profit factor, drawdown |
| `/scan` | analizza subito i mercati |
| `/config` | parametri di rischio attivi |
| `/pause` · `/resume` | sospende/riattiva i nuovi segnali |
| `/panic` | chiude tutte le posizioni a mercato e blocca il bot (chiede conferma) |

Solo gli utenti in `TELEGRAM_ALLOWED_USERS` possono usare comandi e pulsanti.

## Protezioni

**Rischio**
- Size calcolata in modo che lo stop costi `risk_per_trade_pct` del capitale
  (default 1%), commissioni incluse. Limiti massimi non modificabili: 2% per
  trade, leva 10x, perdita giornaliera 10%.
- Stop giornaliero (default −3%): nuove operazioni bloccate fino al giorno dopo.
- Pausa automatica dopo N perdite consecutive (default 4), riattivabile con `/resume`.
- Massimo di posizioni aperte, una sola per simbolo, cooldown tra i segnali.
- Scartati i segnali con SL troppo largo o con R:R netto commissioni insufficiente.

**Esecuzione (live)**
- Gli ordini reali richiedono `mode = "live"` **e** `LIVE_TRADING_CONFIRM=HO_CAPITO_I_RISCHI` nel `.env`.
- Testnet attivo di default; conferma manuale di ogni ordine attiva di default.
- Alla conferma, il segnale è annullato se è scaduto o se il prezzo si è mosso più di 0,25R.
- Lo stop loss è un ordine **reduce-only sull'exchange**: protegge anche se il bot si spegne.
- Se l'exchange rifiuta lo stop, la posizione appena aperta viene **chiusa subito**.
- Se il prezzo di liquidazione cadrebbe prima dello stop, la posizione viene chiusa.
- Rete di sicurezza: se il prezzo supera lo stop e la posizione è ancora aperta, chiusura a mercato.
- Break-even: prima si piazza il nuovo stop, poi si cancella il vecchio.
- Solo derivati perpetual (`swap`) in live; margine isolato.
- Stato (kill switch, perdita giornaliera, posizioni) salvato su SQLite: sopravvive ai riavvii.
  All'avvio il bot avvisa delle posizioni sull'exchange che non gestisce.

**Chiavi API**: crea una chiave con **solo** permesso di trading, **prelievi
disattivati** e, se l'exchange lo consente, limitata all'IP del server. Non
committare mai `.env` (è già in `.gitignore`).

## Backtest

```bash
python -m nito.backtest --symbol SOL/USDT:USDT --timeframe 5m --days 90
python -m nito.backtest --csv candele.csv --timeframe 5m   # timestamp,open,high,low,close,volume
```

Usa le stesse regole del bot, con ipotesi conservative (se nella stessa candela
si toccano sia SL sia TP vince lo SL; commissioni su entrata e uscita) e mostra
i risultati per singolo setup.

## Test

```bash
pytest
```

I test coprono indicatori, ogni setup, calcolo di TP/SL, dimensionamento,
blocchi di rischio, simulazione, flusso di conferma Telegram e il broker live
con un exchange simulato (stop rifiutato, liquidazione, break-even, stop di
emergenza). Il broker live **non** è stato provato contro un exchange reale:
provalo sul testnet prima di usare soldi veri.

## Struttura

```
nito/
  strategy.py      setup Scalping The Bull (regole su candele chiuse)
  signals.py       entrata, SL strutturale, TP a R multiplo
  risk.py          size, stop giornaliero, perdite consecutive, cooldown
  engine.py        scansione → segnale → conferma → esecuzione → monitor
  brokers/paper.py simulazione
  brokers/live.py  ordini reali via ccxt con le protezioni sopra
  telegram_bot.py  comandi, pulsanti, job periodici
  backtest.py      backtest da exchange o CSV
  roulette/        roulette tracker (vedi docs/ROULETTE.md)
config.example.toml    parametri (copiare in config.toml)
roulette.example.toml  tavoli del roulette tracker (copiare in roulette.toml)
.env.example           segreti (copiare in .env)
```
