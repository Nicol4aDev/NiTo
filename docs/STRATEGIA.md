# Strategie: i setup di "Scalping The Bull" tradotti in regole

## Fonti e limiti

Il bot riprende le tecniche del canale YouTube italiano **Scalping The Bull**
così come sono descritte pubblicamente negli indicatori TradingView che lo
accompagnano (autore *TheSocialCryptoClub*):

- [Scalping The Bull Indicator](https://www.tradingview.com/script/xoaBykH4-Scalping-The-Bull-Indicator/) (open source)
- [Scalping The Bull PRO Indicator](https://www.tradingview.com/script/df7I6UVp-Scalping-The-Bull-PRO-Indicator/) (invite-only, descrizione pubblica)
- [Scalping The Bull – BullBot for Stock and Forex](https://www.tradingview.com/script/qsazBWhm-Scalping-The-Bull-BullBot-for-Stock-and-Forex/)
- [Scalping The Bull – Two EMA Strategy](https://www.tradingview.com/script/M7e4uwLN/)

Il contenuto dei video della playlista non è stato analizzato direttamente
(YouTube non era raggiungibile dall'ambiente di sviluppo). Le descrizioni del
metodo sono in parte discrezionali: dove serviva una scelta, è indicata qui
sotto come **interpretazione**. Il progetto non è affiliato al canale.

## Elementi comuni

| Elemento | Regola nel bot |
|---|---|
| Medie | EMA 5, 10, 60, 223 (configurabili) |
| Trend ("sfondo") | verde se EMA60 > EMA223, rosso se EMA60 < EMA223 |
| Filtro trend | i setup trend-following vanno solo long con sfondo verde e solo short con sfondo rosso |
| Trigger point | apertura giornaliera, massimo e minimo di ieri (giorno UTC) |
| BTC come indice | con `btc_filter`, un'altcoin va long solo se anche BTC è in trend rialzista (e viceversa) |
| Candele | solo candele chiuse: nessun segnale "che ridisegna" |
| Stop Loss | minimo (massimo per lo short) della candela di ingresso, meno 0,1 ATR |
| Take Profit | entrata ± rischio × `reward_risk` (default 2R; BullBot indica almeno 1,2) |
| Gestione | stop a break-even a +1R, come suggerito ("spostare velocemente lo stop a pareggio") |

## Setup

Descritti dal lato LONG; lo SHORT è lo specchio esatto.

**Viagra** — almeno `viagra_min_closes` (5) chiusure consecutive sotto la EMA10,
poi una candela rialzista che chiude sopra. Fonte: "serie consecutiva di
chiusure sotto la EMA 10, la rottura della media è un segnale di cambio di trend".

**Fake EMA10** — la candela apre sopra la EMA10, il minimo va sotto, la chiusura
torna sopra. *Interpretazione:* la chiusura deve essere anche sopra la EMA60.

**Terzo tocco EMA60 / EMA223** — il prezzo chiude sotto la media e la tocca da
sotto (massimo ≥ EMA, chiusura < EMA) almeno 3 volte; si entra alla prima
chiusura sopra. *Interpretazione:* tocchi su candele adiacenti contano come uno
solo; i tocchi si contano solo nella serie di chiusure sotto la media.

**Shimano** — almeno `shimano_min_closes` (8) chiusure consecutive comprese tra
EMA60 ed EMA223 (compressione). *Interpretazione:* il segnale scatta quando la
candela chiude fuori dalla banda, nella direzione del trade.

**Emergence** — la EMA60 sta per incrociare al rialzo la EMA223 (distanza ≤
`cross_gap_pct`, in diminuzione rispetto a 5 candele prima) e il prezzo chiude
per la prima volta sopra entrambe. Anticipa un'inversione: il filtro trend non
si applica.

**Anti-crossing** — dopo un rialzo la EMA60 sta per incrociare al ribasso la
EMA223, ma un impulso rialzista riporta il prezzo sopra entrambe.

**Downward fake** — negli ultimi `fake_cross_lookback` candele c'è stato un
Death Cross (EMA60 sotto EMA223), ma il prezzo torna sopra la EMA223 e rompe il
massimo di ieri. Lo specchio short ("upward fake") usa Golden Cross e minimo di
ieri.

**Bomb** — candela con variazione ≥ `bomb_threshold_pct` (2%) che attraversa un
trigger point (apertura di oggi, massimo o minimo di ieri).

Non implementati per mancanza di una definizione pubblica abbastanza precisa:
"Bud" e la "Two EMA Strategy" su 4H (periodi delle EMA non indicati).

## Filtri di qualità del segnale

1. Due setup in direzioni opposte sulla stessa candela si annullano.
2. `min_confluence` > 1 richiede più setup concordi.
3. SL più stretto di `min_sl_pct` viene allargato; più largo di `max_sl_pct` scarta il segnale.
4. Il segnale viene scartato se il R:R **al netto delle commissioni** è sotto
   `min_net_reward_risk`. Sullo scalping a 1–5 minuti le commissioni sono spesso
   il costo principale: questo filtro elimina molti segnali, ed è voluto.
5. Cooldown per simbolo e massimo una posizione per simbolo.

## Come valutare un setup

Prima di fidarti di un setup, fai il backtest su più mesi e più mercati:

```bash
python -m nito.backtest --symbol SOL/USDT:USDT --timeframe 5m --days 90
```

Il report mostra, per ogni setup, numero di operazioni, win rate, media in R e
profit factor. Disattiva in `strategy.enabled` i setup con media R negativa sui
tuoi mercati. Con meno di ~100 operazioni i numeri sono poco affidabili.
