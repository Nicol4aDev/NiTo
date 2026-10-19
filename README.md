# Roulette tracker — bot Telegram

Bot Telegram che registra i numeri usciti su più tavoli di roulette, di
provider diversi (Playtech Live, Evolution, …), mostra
statistiche e serie in corso e **ti avvisa quando un colore (o un'altra
categoria) esce N volte di fila**, con N scelto da te.

> ℹ️ Ogni giro è indipendente: dopo 10 rossi di fila il nero ha la stessa
> probabilità di sempre (18/37 sulla ruota europea). Il bot registra cosa è
> successo, non prevede cosa succederà. Se il gioco smette di essere un
> divertimento, in Italia c'è il Telefono Verde Nazionale per le problematiche
> legate al gioco d'azzardo (ISS): 800 55 88 22.

## Esempio di alert

```
🎰 Immersive Roulette · Evolution
🚨 🔴 Rosso: 6 di fila
Uscito: 🔴32
Ultimi: 🔴32 🔴19 🔴21 🔴25 🔴34 🔴3 ⚫15 🟢0 ⚫22 🔴14
```

Dopo l'alert, se la serie continua arriva `➕ continua, 7 di fila`; quando si
interrompe arriva `✋ serie interrotta dopo 9`.

## Token e utenti autorizzati

Tutto sta in un solo file, **`config.toml`**, nella cartella del progetto:

1. Copia `config.example.toml` e chiama la copia `config.toml`.
2. Aprilo e compila la sezione `[telegram]` in cima:

```toml
[telegram]
token = "123456789:AAH..."     # token del bot, da @BotFather
allowed_users = [123456789]    # il tuo user id, da @userinfobot
```

- Il token si ottiene scrivendo `/newbot` a [@BotFather](https://t.me/BotFather).
- Il tuo user id si ottiene scrivendo `/start` a [@userinfobot](https://t.me/userinfobot).
  Più persone: `allowed_users = [111111, 222222]`.
- `config.toml` contiene il token: non condividerlo (è già escluso da git).

Serve Python 3.11 o superiore; le librerie sono in `requirements.txt`.
Il bot si avvia con `python -m roulette`.

## Comandi

| Comando | Azione |
|---|---|
| `/tavoli` | tavoli raggruppati per provider, ultimi numeri, serie di colore in corso |
| `/ultimi [tavolo] [n]` | ultimi n numeri (default 20), dal più recente |
| `/serie` | serie in corso di almeno 3 su tutti i tavoli, le più lunghe in alto |
| `/serie tavolo` | tutte le serie in corso e i ritardi (giri senza uscire) di un tavolo |
| `/stats [tavolo] [n]` | frequenze, serie più lunghe, numeri caldi e freddi sugli ultimi n giri (default 200) |
| `/alert` | crea un alert con i pulsanti: categoria → quante di fila → tavoli |
| `/alert rosso 6 [tavolo o provider]` | crea un alert in una riga |
| `/alerts` | i tuoi alert, con i pulsanti per eliminarli |
| `/add evo-speed 17 0 32` | registra numeri a mano, dal più vecchio al più recente |
| `/inserisci [tavolo]` | inserimento rapido: ogni messaggio con dei numeri viene registrato |
| `/fine` | esce dall'inserimento rapido |
| `/annulla [tavolo]` | cancella l'ultimo numero inserito a mano |

Il tavolo si indica con il suo id (`evo-speed`), con il nome o con una parte
univoca di id o nome (`prestige`). Se manca, il bot mostra i pulsanti per sceglierlo.

### Alert

```
/alert rosso 6                     6 rossi di fila su qualsiasi tavolo
/alert nero 8 evolution            8 neri di fila sui tavoli Evolution
/alert dispari 5 playtech          5 dispari di fila sui tavoli Playtech Live
/alert verde 2 prestige            due zeri di fila su Prestige Roulette
/alert d3 7                        terza dozzina 7 volte di fila
```

Categorie: `rosso`, `nero`, `verde` (lo zero), `pari`, `dispari`, `basso`
(1-18), `alto` (19-36), `d1` `d2` `d3` (dozzine), `c1` `c2` `c3` (colonne).
Lo zero interrompe le serie di tutte le categorie tranne `verde`, come al tavolo.

- N va da 2 a 50; i pulsanti propongono i valori più usati.
- Gli alert valgono per la chat in cui li crei: in un gruppo arrivano al gruppo.
- Più alert sovrapposti nella stessa chat producono un solo messaggio per giro.
- Se arrivano più giri insieme (una sequenza inserita a mano, un feed in
  ritardo) il bot confronta lo stato prima e dopo e manda un solo messaggio.
- In `[general]` di `config.toml` puoi spegnere gli avvisi di continuazione
  (`notify_continuation`) e di interruzione (`notify_break`).

## Tavoli e fonti dei risultati

I tavoli sono elencati in `config.toml` (`[[tables]]`); ognuno ha una `source`:

| source | Come funziona |
|---|---|
| `manual` | i numeri si inseriscono da Telegram con `/add` o `/inserisci` |
| `simulator` | un numero casuale ogni `interval_sec`: serve a provare il bot |
| `http_json` | il bot interroga un feed JSON ogni `interval_sec` |

Evolution, Pragmatic Play, Playtech ed Ezugi **non pubblicano API aperte** con i
risultati dei tavoli: il bot non contiene scraper di siti di terzi, che
cambiano spesso e di solito vietano l'uso automatico nei termini di servizio.
La sorgente `http_json` è generica: va puntata verso un servizio a cui hai
accesso e che sei autorizzato a usare.

### Configurare un feed JSON

Se il feed risponde, per esempio:

```json
{"data": {"results": [
  {"gameId": "a91f", "result": {"number": 17}},
  {"gameId": "a91e", "result": {"number": 0}}
]}}
```

con il giro più recente per primo:

```toml
[[tables]]
id = "evo-xxxtreme"
name = "XXXtreme Lightning Roulette"
provider = "Evolution"
source = "http_json"
url = "https://esempio.it/api/xxxtreme/ultimi"
interval_sec = 15
items_path = "data.results"   # lista dei giri; "" se la risposta è già la lista
number_path = "result.number" # numero dentro ogni giro (intero o testo, "00" per l'americana)
id_path = "gameId"            # id univoco del giro: evita di contarlo due volte
newest_first = true           # false se la lista va dal più vecchio al più recente
```

- I percorsi sono separati da punti; per gli elementi di una lista si usa
  l'indice (`games.0.number`).
- Al primo recupero dopo l'avvio i giri presenti nel feed riempiono lo storico
  **senza** alert (sarebbero serie già vecchie).
- Se il feed non risponde, il bot lo scrive nel log e riprova all'intervallo successivo.

## Dati

Giri e alert sono salvati su SQLite (`roulette.sqlite3`) e sopravvivono ai
riavvii. Serie e ritardi si calcolano sugli ultimi `history` giri (default 500)
di ogni tavolo; `/stats` può leggere fino a 10.000 giri.

## Test

```bash
pip install -e ".[dev]"
pytest
```

## Struttura

```
roulette/
  wheel.py       numeri, colori, categorie, serie e ritardi
  config.py      lettura di config.toml
  store.py       SQLite: giri e alert
  tracker.py     registra i giri e decide quali alert inviare
  sources.py     feed JSON e simulatore
  formatting.py  messaggi
  bot.py         comandi, pulsanti, job periodici
config.example.toml   configurazione di esempio (copiare in config.toml)
requirements.txt      librerie necessarie
```
