# Bot Trovaunposto → notifiche Telegram

Bot Telegram che controlla **trovaunposto.it** e ti avvisa quando compare un
biglietto treno che rispetta i tuoi criteri (tratta, giorno, fascia oraria,
prezzo massimo).

- **Input guidato a bottoni**: premi *➕ Nuova ricerca* e il bot ti chiede tutto
  passo passo (partenza, arrivo, giorno, orario, prezzo) — niente formati da
  ricordare.
- **Controllo quasi in tempo reale** (ogni ~60 secondi).
- Gira **24/7** su un piccolo server cloud (Railway), anche a PC spento.

---

## Come funziona

Lo script `trovaunposto_live.py` è un bot Telegram sempre attivo che:

1. risponde ai tuoi comandi/bottoni per gestire le ricerche;
2. ogni ~60 secondi apre le tue ricerche su trovaunposto.it, legge i biglietti in
   pagina e tiene solo quelli che rispettano i filtri (direzione, giorno, orario,
   prezzo);
3. confronta con i biglietti già visti e, se ne trova di **nuovi**, ti manda un
   messaggio con prezzo, orario e **link diretto** al biglietto.

Le ricerche e la memoria dei biglietti già visti sono salvate nella cartella
indicata da `DATA_DIR` (su Railway è un disco persistente montato su `/data`).

---

## File del progetto

| File | A cosa serve |
|---|---|
| `trovaunposto_live.py` | Il bot (codice principale). |
| `requirements.txt` | Dipendenze Python. |
| `Procfile` | Comando di avvio per Railway (`python trovaunposto_live.py`). |
| `GUIDA_BOT_LIVE.md` | Guida passo-passo per metterlo online su Railway. |
| `.gitignore`, `.gitattributes` | Impostazioni del repository. |

---

## Comandi e pulsanti del bot

- **➕ Nuova ricerca** (o `/aggiungi`): crea una ricerca guidata dai bottoni.
- **📋 Le mie ricerche** (o `/lista`): vedi e rimuovi le ricerche.
- **⏸️ Pausa / ▶️ Riprendi** (o `/pausa`, `/riprendi`): ferma/riattiva gli avvisi.
- `/stato`: stato e numero di ricerche.
- `/aiuto`: menù principale.

Al primo avvio (e quando aggiungi una ricerca) il bot registra i biglietti
**già presenti** senza avvisarti, poi ti notifica solo i **nuovi**.

---

## Configurazione (variabili d'ambiente)

| Variabile | Obbligatoria | Valore |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | sì | Token del bot di @BotFather. |
| `TELEGRAM_CHAT_ID` | sì | Il tuo id numerico (riceve gli avvisi ed è l'unico autorizzato). |
| `DATA_DIR` | consigliata | Cartella dati persistenti (su Railway: `/data`). |
| `CHECK_INTERVAL` | opzionale | Secondi tra un controllo e l'altro (default 60). |

---

## Metterlo online

Vedi **`GUIDA_BOT_LIVE.md`** per la procedura completa su Railway (deploy dal
repository GitHub, variabili, disco persistente).

### Provarlo in locale (facoltativo)

```bash
pip install -r requirements.txt
set TELEGRAM_BOT_TOKEN=123456789:AA...      # su Windows (PowerShell: $env:...)
set TELEGRAM_CHAT_ID=653739884
python trovaunposto_live.py
```

---

## Note

- Il bot deve girare in **una sola copia** (1 replica): due istanze con lo stesso
  token entrerebbero in conflitto su Telegram e manderebbero notifiche doppie.
- Legge solo pagine pubbliche del sito, a ritmo moderato, per uso personale.
  L'acquisto del biglietto va sempre completato a mano da te sul sito.
