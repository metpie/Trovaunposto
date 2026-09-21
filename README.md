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
| `tests/` | Test automatici (`pip install -r requirements-dev.txt` e poi `python -m pytest`). |

---

## Comandi e pulsanti del bot

Sotto la casella di testo c'è una **tastiera fissa** con le azioni principali (Cerca ora, Nuova ricerca, Le mie ricerche, Pausa/Riprendi; per l'amministratore anche Invita e Utenti). I comandi `/...` compaiono da soli nel menù di Telegram: il bot li registra all'avvio, quindi **non serve impostarli in BotFather** (se l'hai fatto, vengono sostituiti).

- **🔎 Cerca ora** (o `/cerca`): consulta i biglietti per una tratta/giorno **senza salvare** la ricerca.
- **➕ Nuova ricerca** (o `/aggiungi`): crea una ricerca guidata dai bottoni (con avvisi sui nuovi).
- **📋 Le mie ricerche** (o `/lista`): vedi e rimuovi le ricerche.
- **⏸️ Pausa / ▶️ Riprendi** (o `/pausa`, `/riprendi`): ferma/riattiva gli avvisi. Creare una nuova ricerca mentre sei in pausa riattiva gli avvisi da sola (il bot te lo dice).
- `/stato`: stato e numero di ricerche.
- `/pulisci` (o `/clear`): cancella tutti i messaggi della chat degli ultimi 2 giorni (Telegram non permette ai bot di rimuovere quelli più vecchi: per quelli usa “Cancella chat” dal menù di Telegram).
- `/aiuto`: menù principale.
- `/invita` (solo amministratore): genera un codice d'invito monouso valido 24 ore e un link da inoltrare.
- `/utenti` (solo amministratore): elenca le persone invitate (con chi ha gli avvisi in pausa) e permette di revocare l'accesso (le loro ricerche vengono cancellate).

Prima di salvare una ricerca il bot mostra un riepilogo da confermare; dopo il salvataggio puoi aggiungere il ritorno con un tocco.

Ogni persona ha le **proprie** ricerche, la propria pausa e riceve solo i propri avvisi.
Tetto di ricerche attive: 5 per l'amministratore, 3 per ogni invitato (modificabile, vedi sotto).

Al primo avvio (e quando aggiungi una ricerca) il bot registra i biglietti
**già presenti** senza avvisarti, poi ti notifica solo i **nuovi**.

---

## Configurazione (variabili d'ambiente)

| Variabile | Obbligatoria | Valore |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | sì | Token del bot di @BotFather. |
| `TELEGRAM_CHAT_ID` | sì | Il tuo id numerico: sei l'amministratore (puoi invitare altri con /invita). |
| `DATA_DIR` | consigliata | Cartella dati persistenti (su Railway: `/data`). |
| `CHECK_INTERVAL` | opzionale | Secondi tra un controllo e l'altro (default 60). |
| `MAX_SEARCHES_ADMIN` | opzionale | Ricerche attive massime per l'amministratore (default 5). |
| `MAX_SEARCHES_GUEST` | opzionale | Ricerche attive massime per ogni invitato (default 3). |

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
