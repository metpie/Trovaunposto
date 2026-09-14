# Design: bot multiutente con inviti

Data: 2026-09-14
Stato: approvato dall'utente in fase di brainstorming

## Obiettivo

Permettere di condividere il bot Telegram con altre persone. Ogni persona ha
le proprie ricerche, la propria pausa e riceve solo i propri avvisi.
L'amministratore (Matteo) invita le persone con un codice monouso e può
revocare l'accesso. Ogni persona ha un tetto di ricerche attive.

## Decisioni prese

| Domanda | Scelta |
|---|---|
| Modello di condivisione | Ricerche separate per persona, nessuna condivisione tra utenti |
| Autorizzazione | Invito con codice monouso generato dall'admin da Telegram |
| Gestione invitati | L'admin vede la lista e può revocare (le ricerche dell'invitato vengono cancellate) |
| Limiti | 5 ricerche attive per l'admin, 3 per ogni invitato, entrambi regolabili da env |
| Struttura codice | Un solo file `trovaunposto_live.py`, modifiche mirate; niente split in moduli |

## 1. Dati salvati

### `searches.json` (versione 2)

```json
{
  "version": 2,
  "users": {
    "653739884": {
      "name": "Matteo",
      "role": "admin",
      "searches": [ ... ],
      "paused": false,
      "joined": 1757800000.0
    },
    "111222333": {
      "name": "Anna",
      "role": "guest",
      "searches": [ ... ],
      "paused": false,
      "joined": 1757850000.0
    }
  },
  "invites": {
    "K7P3XZ": { "created": 1757850000.0, "expires": 1757936400.0 }
  }
}
```

- La chiave di `users` è l'ID Telegram come stringa (in chat privata coincide
  con il chat id, quindi si usa anche per inviare i messaggi).
- `searches` mantiene lo stesso formato di oggi (output di `make_search`).
- `name` è il nome visualizzato di Telegram al momento dell'ingresso; viene
  aggiornato a ogni messaggio se cambia.
- L'admin è sempre `TELEGRAM_CHAT_ID`. Se non è presente in `users` viene creato
  al volo con `role: admin` al primo messaggio o all'avvio.

**Migrazione dal formato 1** (`{"searches": [...], "paused": bool}`): al
caricamento, se manca `version` o vale 1, le ricerche e la pausa vengono
spostate sotto `users[TELEGRAM_CHAT_ID]` con `role: admin`, e il file viene
riscritto in versione 2. Funzione pura `migrate_store(old, owner_id) -> new`.

### `seen.json`

La chiave passa da `"<ticket_id>"` a `"<user_id>:<ticket_id>"`, con il valore
timestamp come oggi. **Migrazione**: le chiavi senza `:` vengono prefissate con
l'ID admin. Funzione pura `migrate_seen(old, owner_id) -> new`. La pulizia per
età (`SEEN_RETENTION_DAYS`) resta invariata. Alla revoca di un utente si
eliminano tutte le chiavi con il suo prefisso.

## 2. Autorizzazione

- `get_user(update, store) -> dict | None`: restituisce il record utente se
  `effective_user.id` è l'admin o è in `users`; altrimenti `None`.
  Sostituisce `is_owner` in tutti gli handler. Se l'admin non ha ancora un
  record, lo crea.
- Tutti gli handler esistenti (comandi, menù, wizard) lavorano sul record
  dell'utente corrente, non più su `store["searches"]` / `store["paused"]`.
- Utente non autorizzato che scrive un messaggio di testo: se il testo (dopo
  trim e upper) è un codice d'invito valido -> viene registrato (vedi §3);
  altrimenti risposta unica: "Questo bot è privato. Se hai un codice d'invito
  scrivilo qui." Callback da non autorizzati: `answer()` e nessuna azione.
- `/debug`, `/invita`, `/utenti`: solo admin. Gli altri ricevono silenzio.

## 3. Inviti e revoca

### Creazione (`/invita`, solo admin)

- Genera un codice di 6 caratteri dall'alfabeto `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`
  (senza caratteri ambigui), con `secrets.choice`.
- Salva in `invites` con `created = now` e `expires = now + 24h`.
- Risposta: codice in `<code>` e link `https://t.me/<bot_username>?start=<codice>`
  (`bot_username` da `context.bot.username`), con istruzione "Inoltra questo
  link alla persona da invitare. Vale 24 ore, una sola volta."
- Gli inviti scaduti vengono eliminati ogni volta che si tocca `invites`
  (funzione `purge_invites(store, now)`).

### Riscatto

- Via deep link: `/start CODICE` (`context.args[0]`).
- Via testo: messaggio di testo di un non autorizzato uguale a un codice.
- `redeem_invite(store, code, user_id, name, now) -> bool` (pura): se il codice
  esiste e non è scaduto, lo rimuove da `invites`, crea `users[user_id]` con
  `role: guest`, restituisce `True`. Codice sbagliato o scaduto -> `False` e
  risposta "Codice non valido o scaduto."
- Un utente già autorizzato che manda `/start CODICE` ignora il codice e vede
  il menù normale.
- Dopo il riscatto l'invitato riceve `WELCOME` con il menù. L'admin riceve
  "👤 <nome> è entrato con il codice <codice>."

### Lista e revoca (`/utenti`, solo admin)

- Elenco degli invitati: nome, numero di ricerche, data di ingresso. Un bottone
  "🗑 Revoca <nome>" per riga (`callback revoke:<id>`). Se non ci sono invitati:
  "Nessun invitato. Usa /invita per generare un codice."
- Il bottone porta a una conferma: "Revocare l'accesso a <nome>? Le sue
  N ricerche verranno cancellate." con bottoni "✅ Sì, revoca"
  (`revoke_ok:<id>`) e "❌ Annulla" (`revoke_no`).
- `revoke_user(store, seen, user_id) -> (store, seen)` (pura): rimuove
  `users[user_id]` e le chiavi di `seen` con prefisso `user_id:`. L'admin non
  può revocare se stesso (il bottone non viene mostrato per lui).
- Dopo la revoca il bot manda all'ex utente "Il tuo accesso al bot è stato
  revocato." (errori di invio ignorati) e aggiorna la lista per l'admin.

## 4. Limiti per persona

- Env: `MAX_SEARCHES_ADMIN` (default 5), `MAX_SEARCHES_GUEST` (default 3).
- `search_limit(user) -> int` in base al ruolo.
- Il controllo scatta all'avvio del wizard in modalità `add` (`wiz_start_add`),
  prima di fare domande: se `len(user["searches"]) >= limite`, risposta
  "Hai raggiunto il massimo di N ricerche attive. Rimuovine una da
  📋 Le mie ricerche." e `ConversationHandler.END`.
- Stesso controllo ripetuto in `_finish` per sicurezza (se nel frattempo
  qualcosa è cambiato), con lo stesso messaggio.
- "Cerca ora" (`mode == "search"`) non è soggetto al limite.

## 5. Controllo periodico e avvisi

- `check_job` scorre `store["users"]`; salta gli utenti con `paused`; per ogni
  ricerca chiama `find_matches` e manda gli avvisi a `chat_id = user_id`.
- Chiave `seen`: `f"{user_id}:{card['id']}"`.
- La logica di auto-rallentamento (fail_streak, backoff) resta globale e
  invariata; i due messaggi "sito non risponde" e "sito risponde di nuovo"
  vengono inviati a **tutti** gli utenti.
- `on_startup`: migrazione dei due file, registrazione silenziosa iniziale dei
  biglietti per ogni utente (come oggi, ma con chiavi prefissate), messaggio di
  benvenuto solo all'admin.
- `/stato`: per tutti mostra stato pausa e numero di ricerche proprie (con il
  limite, es. "Ricerche attive: 2/3"). All'admin aggiunge "Utenti: N ·
  Ricerche totali: M".
- `/pulisci` funziona già per chat e resta invariato (solo con autorizzazione
  aggiornata).

## 6. Handler da registrare (novità)

- `CommandHandler("invita", cmd_invite)`
- `CommandHandler("utenti", cmd_users)`
- `CallbackQueryHandler(on_menu, pattern=...)` esteso con
  `revoke:\d+|revoke_ok:\d+|revoke_no`
- `cmd_start` gestisce `context.args` per il deep link.
- `MessageHandler(filters.TEXT & ~filters.COMMAND, on_unauthorized_text)` in
  un gruppo successivo (group 1), che agisce solo se l'utente non è
  autorizzato; per gli autorizzati non fa nulla (il wizard, in group 0, gestisce
  già i testi durante la conversazione).

## 7. Test

Nuovo file `tests/test_multiuser.py` (pytest, importa il modulo con env di
test impostate). Copre le funzioni pure:

- `migrate_store`: formato 1 -> 2 conserva ricerche e pausa sotto l'admin;
  formato 2 resta invariato; file assente -> struttura vuota con admin.
- `migrate_seen`: chiavi senza prefisso vengono attribuite all'admin; chiavi già
  prefissate restano.
- `new_invite` / `redeem_invite` / `purge_invites`: codice valido consumato una
  sola volta; scaduto rifiutato e ripulito; codice inesistente rifiutato.
- `search_limit`: 5 per admin, 3 per guest, override da env.
- `revoke_user`: rimuove utente, ricerche e voci `seen` del solo utente
  revocato.

Gli handler Telegram si verificano a mano con il bot in locale (invito, ingresso
del secondo utente, ricerche separate, revoca).

## 8. Documentazione

- README: nuovi comandi `/invita`, `/utenti`; nuove variabili
  `MAX_SEARCHES_ADMIN`, `MAX_SEARCHES_GUEST`; nota che le ricerche sono per
  persona.
- Docstring in testa a `trovaunposto_live.py`: aggiornare l'elenco variabili.
- `GUIDA_BOT_LIVE.md`: aggiungere le due variabili opzionali.

## Fuori scope

- Condivisione di una singola ricerca tra utenti.
- Invitati che possono invitare altri.
- Deduplicazione delle richieste al sito per ricerche identiche di utenti
  diversi.
- Gruppi Telegram: il bot resta pensato per chat private.
