# Bot "sempre attivo" — guida al deploy su Railway

Questa è la versione **interattiva e quasi in tempo reale** del bot
(`trovaunposto_live.py`):

- **Input a bottoni**: premi *➕ Nuova ricerca* e il bot ti guida passo passo
  (partenza, arrivo, giorno, fascia oraria, prezzo) — niente formati da ricordare.
- **Controllo ogni ~60 secondi** invece che ogni 5 minuti.
- Gira **24/7** su un piccolo server cloud (Railway), anche a PC spento.

> ⚠️ Importante: il bot "sempre attivo" e il vecchio sistema su GitHub Actions
> **non possono funzionare insieme** (si contendono gli stessi messaggi di
> Telegram). Prima di avviare Railway, **disattiva il workflow** di GitHub
> (passo 1).

---

## Passo 1 — Disattiva il vecchio workflow (5 minuti)

1. Su GitHub apri il repository → scheda **Actions**.
2. A sinistra clicca **Controllo biglietti Trovaunposto**.
3. In alto a destra apri il menù **…** → **Disable workflow**.

Così il vecchio bot a 5 minuti smette di girare e non disturba quello nuovo.

---

## Passo 2 — Carica i nuovi file su GitHub

I file nuovi/aggiornati in questa cartella sono:

```
trovaunposto_live.py     (il nuovo bot)
requirements.txt         (aggiornato)
Procfile                 (comando di avvio)
```

Con **GitHub Desktop**: scrivi un messaggio (es. "bot live"), **Commit to main**,
poi **Push origin**. (I file appariranno nel repository su GitHub.)

---

## Passo 3 — Crea il servizio su Railway

1. Vai su **railway.app** e **accedi con GitHub** (così Railway vede il tuo
   repository). La registrazione/il piano li gestisci tu: per tenerlo acceso 24/7
   serve il piano **Hobby (~5$/mese)**.
2. **New Project → Deploy from GitHub repo** → autorizza e scegli il repository
   **Trovaunposto**.
3. Railway riconosce Python e installa da solo le dipendenze.

### Imposta il comando di avvio
Nel servizio: **Settings → Deploy → Custom Start Command**:

```
python trovaunposto_live.py
```

### Imposta le variabili (Variables)
Aggiungi (i valori sono gli stessi dei Secret di GitHub):

| Nome | Valore |
|---|---|
| `TELEGRAM_BOT_TOKEN` | il token di @BotFather (bot @trovaposti_bot) |
| `TELEGRAM_CHAT_ID` | `653739884` |
| `DATA_DIR` | `/data` |
| `CHECK_INTERVAL` | `60` |

### Aggiungi un disco persistente (Volume)
Serve perché il bot ricordi le tue ricerche anche dopo un riavvio.
Nel servizio: **Settings → Volumes → + New Volume**, percorso di mount: `/data`.

---

## Passo 4 — Avvia e prova

1. Railway fa il **Deploy** automatico. Apri i **Deploy Logs**: deve comparire
   `Bot in ascolto…`.
2. Sul telefono ti arriva subito il messaggio di benvenuto con i pulsanti.
3. Premi **➕ Nuova ricerca** e crea la tua tratta con i bottoni.

Da qui in poi il bot controlla ogni ~60 secondi e ti avvisa appena compare un
biglietto che rispetta i criteri.

---

## Comandi e pulsanti

- **➕ Nuova ricerca** (o `/aggiungi`): crea una ricerca guidata dai bottoni.
- **📋 Le mie ricerche** (o `/lista`): vedi e rimuovi le ricerche.
- **⏸️ Pausa / ▶️ Riprendi** (o `/pausa`, `/riprendi`): ferma/riattiva gli avvisi.
- `/stato`: mostra stato e numero di ricerche.
- `/aiuto`: menù principale.

---

## Note

- Al primo avvio (e quando aggiungi una ricerca) il bot registra i biglietti
  **già presenti** senza avvisarti, poi ti notifica solo i **nuovi**.
- Se vuoi tornare al sistema gratuito a 5 minuti, riattiva il workflow su GitHub
  (Actions → Enable workflow) e metti in pausa/ferma il servizio Railway. Ricorda:
  uno alla volta, mai entrambi insieme.
- Il controllo ogni 60 secondi è un buon compromesso; puoi cambiarlo con la
  variabile `CHECK_INTERVAL` (in secondi).
