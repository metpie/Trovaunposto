# Design: comandi registrati dal bot e usabilità

Data: 2026-09-14
Stato: approvato ("ok vai" alla proposta in chat)

## Obiettivo

1. Il bot registra da solo l'elenco dei comandi su Telegram, differenziato per
   ruolo (sostituisce l'elenco impostato a mano in BotFather).
2. Il bot diventa più facile da usare per chi non lo conosce: tastiera fissa
   sotto la casella di testo, riepilogo prima di salvare, ritorno in un tocco.

## 1. Comandi registrati all'avvio

- All'avvio (`on_startup`) il bot chiama `set_my_commands` due volte:
  - scope predefinito (tutti): `cerca`, `aggiungi`, `lista`, `pausa`,
    `riprendi`, `stato`, `aiuto`;
  - scope chat dell'admin (`BotCommandScopeChat(OWNER)`): gli stessi più
    `invita`, `utenti`, `pulisci`.
- Le descrizioni sono in italiano, brevi (max ~30 caratteri), definite in una
  costante `COMMANDS_BASE` e `COMMANDS_ADMIN` (liste di `(comando, descrizione)`).
- Funzione pura `commands_for(role) -> list[tuple[str, str]]` (base per guest,
  base + admin per admin), usata sia dalla registrazione sia dai test.
- I sinonimi (`/help`, `/ricerche`, `/riattiva`, `/clear`) e `/debug` restano
  funzionanti ma non compaiono nell'elenco.
- Errori di `set_my_commands` vengono loggati e non bloccano l'avvio.

## 2. Tastiera fissa (reply keyboard)

- `main_kb(user) -> ReplyKeyboardMarkup` con `resize_keyboard=True`,
  `is_persistent=True`:
  - riga 1: `🔎 Cerca ora`, `➕ Nuova ricerca`
  - riga 2: `📋 Le mie ricerche`, `⏸️ Pausa` oppure `▶️ Riprendi` (in base a `user["paused"]`)
  - riga 3 (solo admin): `🔗 Invita`, `👥 Utenti`
- Le etichette sono costanti (`BTN_FIND`, `BTN_NEW`, `BTN_LIST`, `BTN_PAUSE`,
  `BTN_RESUME`, `BTN_INVITE`, `BTN_USERS`); `MENU_LABELS` è la lista completa.
- Un `MessageHandler(filters.Text(MENU_LABELS), on_menu_text)` in group 0
  instrada ogni etichetta all'azione corrispondente (stessa logica dei comandi).
  Per `BTN_FIND`/`BTN_NEW` il wizard parte come da comando: quindi questi due
  vanno come `entry_points` del `ConversationHandler` (con `filters.Text([...])`).
- I filtri di testo degli stati del wizard escludono le etichette
  (`filters.TEXT & ~filters.COMMAND & ~filters.Text(MENU_LABELS)`), e le
  etichette sono anche `fallbacks` del wizard: premere un bottone del menù
  durante il wizard annulla il wizard ed esegue l'azione.
- La tastiera viene (ri)inviata con: il benvenuto (`/start`, `/aiuto`), il
  messaggio dopo pausa/riprendi (l'etichetta del bottone cambia), l'ingresso di
  un invitato. Il vecchio menù inline `main_menu_kb` sparisce dal benvenuto; i
  callback `find|new|list|pause|resume` restano per i bottoni inline ancora
  presenti (es. "➕ Nuova ricerca" nella lista).
- Chi non è autorizzato riceve `PRIVATE_MSG` con `ReplyKeyboardRemove()`, e
  alla revoca il messaggio "accesso revocato" toglie la tastiera.

## 3. Riepilogo prima di salvare (solo "Nuova ricerca")

- Nuovo stato `ASK_CONFIRM` dopo il prezzo, solo in `mode == "add"`.
  In `mode == "search"` si passa direttamente a `_finish` come oggi.
- Testo: `📝 <b>Riepilogo</b>\n<search_summary>\nConfermi?` con tastiera inline
  `✅ Conferma` (`confirm|ok`), `🔁 Ricomincia` (`confirm|restart`),
  `❌ Annulla` (`confirm|cancel`).
- `ok` → `_finish`; `restart` → riparte da `wiz_start` con lo stesso `mode`;
  `cancel` → "Operazione annullata." e `END`.

## 4. Ritorno in un tocco

- Dopo il salvataggio di una ricerca (in `_finish`, mode add), il messaggio
  finale porta una tastiera inline con `🔁 Aggiungi anche il ritorno`
  (`return|<dep>|<arr>` non serve: si salva in
  `context.user_data["last_route"] = {"dep": ..., "arr": ...}` e il callback è
  `^return$`).
- Il callback `return` è un `entry_point` del wizard: controlla autorizzazione e
  limite come `wiz_start_add` (il ritorno conta come ricerca a sé), imposta
  `mode = "add"`, `draft = {"dep": arr, "arr": dep}` invertiti, e va
  direttamente allo stato `ASK_DAY` con il testo
  `🔁 <b>Ritorno: {arr} → {dep}</b>\nChe giorno?`.
- Se `last_route` manca (riavvio del bot), il bot risponde "Ricomincia da
  ➕ Nuova ricerca." e termina.

## 5. Chiusura di ogni azione

- Dopo "Cerca ora" (mode search) il messaggio finale porta inline
  `➕ Salva come ricerca` (`callback new`, avvia il wizard normale) — un solo
  bottone, niente altro.
- Le risposte di `/stato`, lista, pausa/riprendi non cambiano oltre a quanto
  detto in §2.

## Test (pytest, funzioni pure)

- `commands_for("guest")` non contiene `invita`/`utenti`; `commands_for("admin")`
  li contiene ed è un superinsieme; nessun duplicato; descrizioni ≤ 40 caratteri.
- `main_kb(user)` per guest: 2 righe, etichetta pausa/riprendi coerente con
  `paused`; per admin: 3 righe con `BTN_INVITE`/`BTN_USERS`.
- `MENU_LABELS` contiene tutte le costanti `BTN_*` e sono uniche.
- `reverse_route(draft) -> draft` scambia dep/arr.
- `confirm_text(search)` contiene il riepilogo di `search_summary`.

## Fuori scope

- Cambiare il flusso a cinque passi del wizard.
- Modificare i testi degli avvisi sui biglietti.
- `set_chat_menu_button` (il pulsante "Menu" di Telegram mostra già i comandi).
