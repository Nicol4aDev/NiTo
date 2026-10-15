# Roulette tracker — bot Telegram

Bot Telegram che registra i numeri usciti su più tavoli di roulette, di
provider diversi (Evolution, Pragmatic Play, Playtech, Ezugi, …), mostra
statistiche e serie in corso e **ti avvisa quando un colore (o un'altra
categoria) esce N volte di fila**, con N scelto da te.

> ℹ️ Ogni giro è indipendente: dopo 10 rossi di fila il nero ha la stessa
> probabilità di sempre (18/37 sulla ruota europea). Il bot registra cosa è
> successo, non prevede cosa succederà. Se il gioco smette di essere un
> divertimento, in Italia c'è il Telefono Verde Nazionale per le problematiche
> legate al gioco d'azzardo (ISS): 800 55 88 22.

## Esempio di alert

```
🎰 Lightning Roulette · Evolution
🚨 🔴 Rosso: 6 di fila
Uscito: 🔴32
Ultimi: 🔴32 🔴19 🔴21 🔴25 🔴34 🔴3 ⚫15 🟢0 ⚫22 🔴14
```

Dopo l'alert, se la serie continua arriva `➕ continua, 7 di fila`; quando si
interrompe arriva `✋ serie interrotta dopo 9`. Entrambi si possono disattivare
in `roulette.toml`.

## Avvio rapido sul tuo PC

1. Installa Python 3.11 o superiore da [python.org](https://www.python.org/downloads/)
   (su Windows spunta **Add python.exe to PATH**).
2. Scarica il progetto ed estrai lo ZIP.
3. Fai doppio clic su `avvia_roulette.py` (su Mac/Linux: `python3 avvia_roulette.py`).
   Al primo avvio installa le librerie in `.venv`, crea `roulette.toml` e ti
   chiede token del bot e user id Telegram, che salva in `.env`.

Il bot resta attivo finché la finestra è aperta e il PC acceso.

## Installazione

Stessa installazione del progetto (vedi [README](../README.md)), poi:

```bash
cp roulette.example.toml roulette.toml
```

Serve un bot Telegram **diverso** da quello dei segnali di trading (due
programmi non possono leggere lo stesso bot): crealo con
[@BotFather](https://t.me/BotFather) e nel file `.env` imposta:

```
ROULETTE_BOT_TOKEN=123456:ABC...
ROULETTE_ALLOWED_USERS=11111111,22222222
```

Se non conosci il tuo user id, avvia il bot e scrivigli `/start`: se non sei
autorizzato ti risponde con il tuo id.

```bash
python -m nito.roulette --check   # valida la configurazione
python -m nito.roulette           # avvia (oppure: nito-roulette)
```

### Su un server (anche solo dal telefono)

Nel repository c'è un `Dockerfile`: servizi come [Railway](https://railway.com)
lo riconoscono da soli. Collega il repository, imposta le variabili
`ROULETTE_BOT_TOKEN` e `ROULETTE_ALLOWED_USERS` e il bot parte. Per non perdere
numeri e alert a ogni nuovo deploy, aggiungi un volume montato su `/data` e la
variabile `ROULETTE_DATABASE=/data/roulette.sqlite3`. Senza un `roulette.toml`
nel repository si usano i tavoli di `roulette.example.toml`.

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
| `/add tavolo 17 0 32` | registra numeri a mano, dal più vecchio al più recente |
| `/inserisci [tavolo]` | inserimento rapido: ogni messaggio con dei numeri viene registrato |
| `/fine` | esce dall'inserimento rapido |
| `/annulla [tavolo]` | cancella l'ultimo numero inserito a mano |

Il tavolo si indica con il suo id (`evo-lightning`), con il nome o con una parte
univoca di id o nome (`mega`). Se manca, il bot mostra i pulsanti per sceglierlo.

### Alert

Esempi:

```
/alert rosso 6                     6 rossi di fila su qualsiasi tavolo
/alert nero 8 evolution            8 neri di fila sui tavoli Evolution
/alert dispari 5 pragmatic play    5 dispari di fila sui tavoli Pragmatic Play
/alert verde 2 mega                due zeri di fila su Mega Roulette
/alert d3 7                        terza dozzina 7 volte di fila
```

Categorie: `rosso`, `nero`, `verde` (lo zero), `pari`, `dispari`, `basso`
(1-18), `alto` (19-36), `d1` `d2` `d3` (dozzine), `c1` `c2` `c3` (colonne).
Lo zero interrompe le serie di tutte le categorie tranne `verde`, come al tavolo.

- N va da 2 a 50; i pulsanti propongono i valori più usati.
- Gli alert valgono per la chat in cui li crei: in un gruppo arrivano al gruppo.
- Più alert sovrapposti nella stessa chat (es. "rosso 6 ovunque" e
  "rosso 8 su Evolution") producono un solo messaggio per giro.
- Se arrivano più giri insieme (una sequenza inserita a mano, un feed in
  ritardo) il bot confronta lo stato prima e dopo e manda un solo messaggio.

## Da dove arrivano i risultati

Ogni tavolo in `roulette.toml` ha una `source`:

| source | Come funziona |
|---|---|
| `manual` | i numeri si inseriscono da Telegram con `/add` o `/inserisci` |
| `simulator` | un numero casuale ogni `interval_sec`: serve a provare il bot |
| `http_json` | il bot interroga un feed JSON ogni `interval_sec` |

Evolution, Pragmatic Play, Playtech ed Ezugi **non pubblicano API aperte** con i
risultati dei tavoli: il bot non contiene scraper di siti di terzi, che
cambiano spesso e di solito vietano l'uso automatico nei termini di servizio.
La sorgente `http_json` è generica: va puntata verso un servizio a cui hai
accesso e che sei autorizzato a usare (un'API del tuo operatore, un tuo
servizio, un abbonamento a un fornitore di dati).

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
[tables.headers]
Authorization = "Bearer ${FEED_TOKEN}"   # ${VAR} viene letto dal .env
```

- I percorsi sono separati da punti; per gli elementi di una lista si usa
  l'indice (`games.0.number`).
- Se la risposta è un solo giro (l'ultimo uscito) invece di una lista, funziona
  lo stesso: basta che `items_path` punti a quell'oggetto.
- Al primo recupero dopo l'avvio i giri presenti nel feed riempiono lo storico
  **senza** alert (sarebbero serie già vecchie); da lì in poi ogni giro nuovo
  viene valutato.
- Se il feed non risponde, il bot lo scrive nel log (non a ogni tentativo) e
  riprova all'intervallo successivo.

## Dati

Giri e alert sono salvati su SQLite (`roulette.sqlite3`): sopravvivono ai
riavvii. Le serie e i ritardi si calcolano sugli ultimi `history` giri (default
500) di ogni tavolo; `/stats` può leggere fino a 10.000 giri dal database.

## Struttura

```
nito/roulette/
  wheel.py       numeri, colori, categorie, serie e ritardi
  config.py      roulette.toml e segreti
  store.py       SQLite: giri e alert
  tracker.py     registra i giri e decide quali alert inviare
  sources.py     feed JSON e simulatore
  formatting.py  messaggi
  bot.py         comandi, pulsanti, job periodici
roulette.example.toml   configurazione di esempio (copiare in roulette.toml)
```
