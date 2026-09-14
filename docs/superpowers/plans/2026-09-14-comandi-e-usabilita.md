# Comandi registrati e usabilità — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Il bot registra da solo i comandi Telegram per ruolo e diventa più facile da usare: tastiera fissa, riepilogo prima di salvare, ritorno in un tocco.

**Architecture:** Tutto in `trovaunposto_live.py`. Costanti per etichette e comandi, funzioni pure testabili (`commands_for`, `main_kb_rows`, `reverse_route`, `confirm_text`, `draft_search`), un handler per la tastiera fissa, uno stato in più nel wizard (`ASK_CONFIRM`) e un entry point `return`.

**Tech Stack:** Python 3.13, python-telegram-bot 21 (`ReplyKeyboardMarkup`, `BotCommand`, `BotCommandScope*`, `filters.Text`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-comandi-e-usabilita-design.md`

## Global Constraints

- Un solo file di codice: `trovaunposto_live.py`.
- Etichette della tastiera (esatte): `🔎 Cerca ora`, `➕ Nuova ricerca`, `📋 Le mie ricerche`, `⏸️ Pausa`, `▶️ Riprendi`, `🔗 Invita`, `👥 Utenti`.
- Comandi elencati per tutti: `cerca, aggiungi, lista, pausa, riprendi, stato, aiuto`; per l'admin in più: `invita, utenti, pulisci`. `/debug` e i sinonimi restano funzionanti ma non elencati.
- Il riepilogo con conferma vale solo per `mode == "add"`; "Cerca ora" non lo ha.
- Il ritorno conta come ricerca a sé (limite applicato).
- Chi non è autorizzato non deve mai ricevere la tastiera fissa (`ReplyKeyboardRemove`).
- Testi in italiano con accenti corretti. Commit in italiano con in coda
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Verifiche per ogni task: `python -m pytest tests -v` (senza nuovi warning), syntax check
  `python -c "import ast; ast.parse(open('trovaunposto_live.py',encoding='utf-8').read()); print('syntax ok')"`,
  e costruzione app `$env:TELEGRAM_BOT_TOKEN='123:test'; $env:TELEGRAM_CHAT_ID='1000'; $env:DATA_DIR='tests/_data_tmp'; python -c "import trovaunposto_live as b; b.build_application(); print('app ok')"`.

---

### Task 1: Comandi registrati all'avvio per ruolo

**Files:**
- Modify: `trovaunposto_live.py` — import da `telegram`; nuove costanti dopo `WELCOME`; nuova `register_commands`; chiamata in `on_startup`
- Test: `tests/test_multiuser.py`

**Interfaces:**
- Produces: `COMMANDS_BASE`, `COMMANDS_ADMIN` (liste di tuple `(comando, descrizione)`), `commands_for(role) -> list[tuple]`, `async register_commands(bot)`.

- [ ] **Step 1: Test (falliscono)**

Appendere a `tests/test_multiuser.py`:
```python
# --- Comandi registrati -------------------------------------------------------

def test_commands_for_roles():
    guest = [c for c, _ in bot.commands_for("guest")]
    admin = [c for c, _ in bot.commands_for("admin")]
    assert guest == ["cerca", "aggiungi", "lista", "pausa", "riprendi", "stato", "aiuto"]
    assert admin[:len(guest)] == guest
    assert set(admin) - set(guest) == {"invita", "utenti", "pulisci"}
    assert len(set(admin)) == len(admin)
    assert "debug" not in admin
    assert all(0 < len(d) <= 40 for _, d in bot.commands_for("admin"))
```
Run: `python -m pytest tests -v -k commands_for` → FAIL `AttributeError`.

- [ ] **Step 2: Implementazione**

Import: cambiare la riga `from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update` in
```python
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
```
Dopo `WELCOME` (prima di `# Stati del wizard`):
```python
# Comandi mostrati nel menù di Telegram (registrati all'avvio, per ruolo).
COMMANDS_BASE = [
    ("cerca", "Guarda i biglietti disponibili ora"),
    ("aggiungi", "Nuova ricerca con avvisi"),
    ("lista", "Le mie ricerche"),
    ("pausa", "Sospendi gli avvisi"),
    ("riprendi", "Riattiva gli avvisi"),
    ("stato", "Stato del bot"),
    ("aiuto", "Menù principale"),
]
COMMANDS_ADMIN = [
    ("invita", "Crea un codice d'invito"),
    ("utenti", "Gestisci gli invitati"),
    ("pulisci", "Cancella i messaggi recenti"),
]


def commands_for(role):
    return COMMANDS_BASE + (COMMANDS_ADMIN if role == "admin" else [])


async def register_commands(bot):
    """Registra l'elenco comandi su Telegram: base per tutti, esteso per l'admin.
    Sovrascrive quanto impostato a mano in BotFather."""
    try:
        await bot.set_my_commands(
            [BotCommand(c, d) for c, d in commands_for("guest")],
            scope=BotCommandScopeDefault())
        await bot.set_my_commands(
            [BotCommand(c, d) for c, d in commands_for("admin")],
            scope=BotCommandScopeChat(chat_id=int(OWNER)))
    except Exception as e:  # noqa: BLE001
        log.warning("registrazione comandi fallita: %s", e)
```
In `on_startup`, subito dopo `store = app.bot_data["store"]`: `await register_commands(app.bot)`.

- [ ] **Step 3: Verifica e commit**

`python -m pytest tests -v` → 19 pass. Syntax e app check. Commit: `Registra i comandi Telegram all'avvio, elenco esteso per l'admin`.

---

### Task 2: Tastiera fissa sotto la casella di testo

**Files:**
- Modify: `trovaunposto_live.py` — import; costanti `BTN_*`/`MENU_LABELS`; `main_kb_rows`/`main_kb` al posto di `main_menu_kb`; `on_menu_text`, `wiz_menu_fallback`; tutti i punti che inviano `WELCOME`; `cmd_pause`/`cmd_resume`; rami `pause`/`resume` di `on_menu`; `PRIVATE_MSG` e revoca con `ReplyKeyboardRemove`; filtri degli stati del wizard; registrazione handler
- Test: `tests/test_multiuser.py`

**Interfaces:**
- Consumes: `is_admin`, `get_user`, `show_list(update, context, user)`, `cmd_pause`, `cmd_resume`, `cmd_invite`, `cmd_users`, `wiz_start_add`, `wiz_start_search`.
- Produces: `BTN_FIND, BTN_NEW, BTN_LIST, BTN_PAUSE, BTN_RESUME, BTN_INVITE, BTN_USERS`, `MENU_LABELS`, `main_kb_rows(user) -> list[list[str]]`, `main_kb(user) -> ReplyKeyboardMarkup`, `MENU_FILTER`.

- [ ] **Step 1: Test (falliscono)**

```python
# --- Tastiera fissa ------------------------------------------------------------

def test_menu_labels_unique_and_complete():
    labels = [bot.BTN_FIND, bot.BTN_NEW, bot.BTN_LIST, bot.BTN_PAUSE,
              bot.BTN_RESUME, bot.BTN_INVITE, bot.BTN_USERS]
    assert bot.MENU_LABELS == labels
    assert len(set(labels)) == len(labels)


def test_main_kb_rows_guest_and_admin():
    guest = bot.new_user("Anna", "guest", NOW)
    assert bot.main_kb_rows(guest) == [[bot.BTN_FIND, bot.BTN_NEW], [bot.BTN_LIST, bot.BTN_PAUSE]]
    guest["paused"] = True
    assert bot.main_kb_rows(guest)[1] == [bot.BTN_LIST, bot.BTN_RESUME]
    admin = bot.new_user("Matteo", "admin", NOW)
    rows = bot.main_kb_rows(admin)
    assert len(rows) == 3 and rows[2] == [bot.BTN_INVITE, bot.BTN_USERS]
```
Run: `python -m pytest tests -v -k "menu_labels or main_kb"` → FAIL.

- [ ] **Step 2: Costanti e tastiera**

Import: aggiungere `ReplyKeyboardMarkup, ReplyKeyboardRemove` all'import da `telegram` (in ordine alfabetico nel blocco).

Sostituire integralmente `main_menu_kb` con:
```python
BTN_FIND = "🔎 Cerca ora"
BTN_NEW = "➕ Nuova ricerca"
BTN_LIST = "📋 Le mie ricerche"
BTN_PAUSE = "⏸️ Pausa"
BTN_RESUME = "▶️ Riprendi"
BTN_INVITE = "🔗 Invita"
BTN_USERS = "👥 Utenti"
MENU_LABELS = [BTN_FIND, BTN_NEW, BTN_LIST, BTN_PAUSE, BTN_RESUME, BTN_INVITE, BTN_USERS]


def main_kb_rows(user):
    rows = [[BTN_FIND, BTN_NEW],
            [BTN_LIST, BTN_RESUME if user.get("paused") else BTN_PAUSE]]
    if is_admin(user):
        rows.append([BTN_INVITE, BTN_USERS])
    return rows


def main_kb(user):
    """Tastiera fissa sotto la casella di testo (sostituisce il vecchio menù inline)."""
    return ReplyKeyboardMarkup(main_kb_rows(user), resize_keyboard=True, is_persistent=True)
```
Nota: `main_kb` va definita dopo `is_admin` (che è nella sezione Inviti, prima degli helper Telegram: ok).

- [ ] **Step 3: Consegna della tastiera**

- `cmd_start`: `reply_markup=main_kb(user)`.
- `_handle_invite_code`: dopo `save_store(store)`: `new_user_rec = store["users"][str(u.id)]` e `reply_markup=main_kb(new_user_rec)`.
- `on_startup`: `reply_markup=main_kb(admin)`.
- `cmd_pause`: `reply_text("⏸️ Notifiche sospese. Usa ▶️ Riprendi per riattivarle.", reply_markup=main_kb(user))`; `cmd_resume`: `reply_text("▶️ Notifiche riattivate.", reply_markup=main_kb(user))`.
- `on_menu` rami `pause`/`resume` (bottoni inline di vecchi messaggi): sostituire `q.edit_message_text(..., reply_markup=main_menu_kb(...))` con `await q.message.reply_text("⏸️ Notifiche sospese.", reply_markup=main_kb(user))` (e l'equivalente per resume).
- Non autorizzati: in `cmd_start` e `on_unauthorized_text`, `reply_text(PRIVATE_MSG, reply_markup=ReplyKeyboardRemove())`; in `_handle_invite_code` il caso codice non valido: `reply_text(INVALID_CODE_MSG, reply_markup=ReplyKeyboardRemove())`.
- Revoca (`on_menu`, ramo `revoke_ok`): `send_message(chat_id=uid, text="Il tuo accesso al bot è stato revocato.", reply_markup=ReplyKeyboardRemove())`.
- Grep finale: `main_menu_kb` non deve più esistere.

- [ ] **Step 4: Handler della tastiera e integrazione col wizard**

Dopo `on_unauthorized_text`:
```python
async def on_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bottoni della tastiera fissa (fuori dal wizard). Cerca/Nuova sono entry point del wizard."""
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return  # ci pensa on_unauthorized_text (gruppo 1)
    text = update.effective_message.text
    if text == BTN_LIST:
        await show_list(update, context, user)
    elif text == BTN_PAUSE:
        await cmd_pause(update, context)
    elif text == BTN_RESUME:
        await cmd_resume(update, context)
    elif text == BTN_INVITE:
        await cmd_invite(update, context)
    elif text == BTN_USERS:
        await cmd_users(update, context)
```
Prima di `wiz_cancel` (o subito dopo):
```python
async def wiz_menu_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Un bottone del menù premuto durante il wizard: annulla il wizard ed esegue l'azione."""
    context.user_data.pop("draft", None)
    context.user_data.pop("mode", None)
    await on_menu_text(update, context)
    return ConversationHandler.END
```
In `build_application`:
```python
    MENU_FILTER = filters.Text(MENU_LABELS)
    WIZ_TEXT = filters.TEXT & ~filters.COMMAND & ~MENU_FILTER
```
- ogni `MessageHandler(filters.TEXT & ~filters.COMMAND, wiz_*_txt)` negli stati → `MessageHandler(WIZ_TEXT, wiz_*_txt)`;
- `entry_points` aggiunge `MessageHandler(filters.Text([BTN_NEW]), wiz_start_add)` e `MessageHandler(filters.Text([BTN_FIND]), wiz_start_search)`;
- `fallbacks=[CommandHandler("annulla", wiz_cancel), MessageHandler(MENU_FILTER, wiz_menu_fallback)]`;
- dopo `app.add_handler(wizard)`: `app.add_handler(MessageHandler(filters.Text([BTN_LIST, BTN_PAUSE, BTN_RESUME, BTN_INVITE, BTN_USERS]), on_menu_text))`.

Nota su `cmd_invite`/`cmd_users`/`cmd_pause`/`cmd_resume`: usano `update.effective_message.reply_text`, quindi funzionano anche quando chiamati da `on_menu_text`.

- [ ] **Step 5: Verifica e commit**

`python -m pytest tests -v` → 21 pass. Syntax, app check, `grep -n main_menu_kb` vuoto. Commit: `Tastiera fissa con le azioni principali (e comandi admin per l'amministratore)`.

---

### Task 3: Riepilogo prima di salvare, ritorno in un tocco, chiusura di "Cerca ora"

**Files:**
- Modify: `trovaunposto_live.py` — stati del wizard; `confirm_text`, `confirm_kb`, `draft_search`, `reverse_route`, `after_add_kb`, `after_search_kb`; `_reject_if_over_limit`; `wiz_start_add`; `wiz_price_btn`/`wiz_price_txt`; `_after_price`; `wiz_confirm_btn`; `wiz_return`; `_finish`; registrazione
- Test: `tests/test_multiuser.py`

**Interfaces:**
- Consumes: `make_search`, `search_summary`, `esc`, `days_kb`, `search_limit`, `limit_msg`, `get_user`.
- Produces: `ASK_CONFIRM`, `draft_search(d) -> search`, `reverse_route(draft) -> dict`, `confirm_text(search) -> str`.

- [ ] **Step 1: Test (falliscono)**

```python
# --- Riepilogo e ritorno ------------------------------------------------------

def test_reverse_route_swaps_cities():
    assert bot.reverse_route({"dep": "Roma", "arr": "Milano", "date": "x"}) == {"dep": "Milano", "arr": "Roma"}
    assert bot.reverse_route({}) == {"dep": "", "arr": ""}


def test_draft_search_and_confirm_text():
    d = {"dep": "Roma", "arr": "Milano", "date": "2026-10-01", "tfrom": "08:00", "tto": "12:00", "maxp": 50}
    s = bot.draft_search(d)
    assert s["name"] == "Roma → Milano" and s["max_price"] == 50
    t = bot.confirm_text(s)
    assert "Riepilogo" in t and "Confermi?" in t and "01/10/2026" in t and "max 50€" in t
```
Run: `python -m pytest tests -v -k "reverse_route or draft_search"` → FAIL.

- [ ] **Step 2: Costanti, helper puri e tastiere**

Sostituire `ASK_DEP, ASK_ARR, ASK_DAY, ASK_TIME, ASK_PRICE = range(5)` con
`ASK_DEP, ASK_ARR, ASK_DAY, ASK_TIME, ASK_PRICE, ASK_CONFIRM = range(6)`.

Dopo `price_kb()` aggiungere:
```python
def confirm_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Conferma", callback_data="confirm|ok")],
        [InlineKeyboardButton("🔁 Ricomincia", callback_data="confirm|restart"),
         InlineKeyboardButton("❌ Annulla", callback_data="confirm|cancel")],
    ])


def after_add_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Aggiungi anche il ritorno", callback_data="return")],
        [InlineKeyboardButton("📋 Le mie ricerche", callback_data="list")],
    ])


def after_search_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Salva come ricerca", callback_data="new")]])


def draft_search(d):
    return make_search(d.get("dep", ""), d.get("arr", ""), d.get("date", ""),
                       d.get("tfrom", ""), d.get("tto", ""), d.get("maxp"))


def reverse_route(draft):
    return {"dep": draft.get("arr", ""), "arr": draft.get("dep", "")}


def confirm_text(search):
    return f"📝 <b>Riepilogo</b>\n{esc(search_summary(search))}\n\nConfermi?"
```

- [ ] **Step 3: Limite condiviso e ritorno**

Sostituire `wiz_start_add` con:
```python
async def _reject_if_over_limit(update, user):
    """True (dopo aver risposto) se l'utente ha già raggiunto il tetto di ricerche."""
    limit = search_limit(user)
    if len(user["searches"]) < limit:
        return False
    if update.callback_query:
        await update.callback_query.message.reply_text(limit_msg(limit), parse_mode=ParseMode.HTML)
    else:
        await update.effective_message.reply_text(limit_msg(limit), parse_mode=ParseMode.HTML)
    return True


async def wiz_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if update.callback_query:
        await update.callback_query.answer()
    if user is None:
        return ConversationHandler.END
    if await _reject_if_over_limit(update, user):
        return ConversationHandler.END
    context.user_data["mode"] = "add"
    return await wiz_start(update, context)


async def wiz_return(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bottone 'Aggiungi anche il ritorno': città invertite, si riparte dal giorno."""
    q = update.callback_query
    await q.answer()
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return ConversationHandler.END
    route = context.user_data.get("last_route")
    if not route:
        await q.message.reply_text("Ricomincia da ➕ Nuova ricerca.")
        return ConversationHandler.END
    if await _reject_if_over_limit(update, user):
        return ConversationHandler.END
    context.user_data["mode"] = "add"
    d = reverse_route(route)
    context.user_data["draft"] = d
    await q.message.reply_text(
        f"🔁 <b>Ritorno: {esc(d['dep'].title())} → {esc(d['arr'].title())}</b>\nChe giorno?",
        parse_mode=ParseMode.HTML, reply_markup=days_kb())
    return ASK_DAY
```
Nota: `wiz_start` fa già `answer()` sul callback; una seconda `answer()` è innocua.

- [ ] **Step 4: Prezzo → riepilogo → conferma**

Le due lambda `send` in `wiz_price_btn` e `wiz_price_txt` diventano `lambda t, **kw: q.message.reply_text(t, parse_mode=ParseMode.HTML, disable_web_page_preview=True, **kw)` (e l'equivalente con `update.message`) e chiamano `_after_price` invece di `_finish`:
```python
async def _after_price(update, context, send):
    if context.user_data.get("mode") == "search":
        return await _finish(update, context, send)
    search = draft_search(context.user_data.get("draft", {}))
    await send(confirm_text(search), reply_markup=confirm_kb())
    return ASK_CONFIRM


async def wiz_confirm_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split("|", 1)[1]
    if val == "ok":
        try:
            await q.edit_message_reply_markup(None)
        except Exception:  # noqa: BLE001
            pass
        return await _finish(update, context,
                             lambda t, **kw: q.message.reply_text(
                                 t, parse_mode=ParseMode.HTML, disable_web_page_preview=True, **kw))
    if val == "restart":
        return await wiz_start(update, context)
    context.user_data.pop("draft", None)
    context.user_data.pop("mode", None)
    await q.edit_message_text("Operazione annullata.")
    return ConversationHandler.END
```

- [ ] **Step 5: `_finish` — chiusure con bottoni e memoria del ritorno**

In `_finish`:
- `search = make_search(...)` → `search = draft_search(d)`.
- ramo `search`: sostituire il messaggio "ℹ️ Solo consultazione…" con
  `await send("ℹ️ Solo consultazione: questa ricerca non è stata salvata.", reply_markup=after_search_kb())`.
- ramo `add`: dopo `save_store(store)` aggiungere `context.user_data["last_route"] = {"dep": d.get("dep", ""), "arr": d.get("arr", "")}`; nel `try` togliere il `if matches: await send("Da ora ti avviserò…")`; dopo il `try/except` (prima dei `pop`) aggiungere sempre
  `await send("Da ora ti avviserò dei <b>nuovi</b> biglietti che compaiono.", reply_markup=after_add_kb())`.
- `wiz_cancel`: aggiungere `context.user_data.pop("mode", None)`.

- [ ] **Step 6: Registrazione**

Nel `ConversationHandler`: entry point `CallbackQueryHandler(wiz_return, pattern=r"^return$")`; stato `ASK_CONFIRM: [CallbackQueryHandler(wiz_confirm_btn, pattern=r"^confirm\|")]`.

- [ ] **Step 7: Verifica e commit**

`python -m pytest tests -v` → 23 pass. Syntax, app check. Grep: `_finish(` chiamata solo da `_after_price` e `wiz_confirm_btn`. Commit: `Riepilogo con conferma, ritorno in un tocco e chiusure con bottoni`.

---

### Task 4: Documentazione

**Files:** `README.md`, `GUIDA_BOT_LIVE.md`

- README, sezione "Comandi e pulsanti del bot": aggiungere in testa
  ```markdown
  Sotto la casella di testo c'è una **tastiera fissa** con le azioni principali (Cerca ora, Nuova ricerca, Le mie ricerche, Pausa/Riprendi; per l'amministratore anche Invita e Utenti). I comandi `/...` compaiono da soli nel menù di Telegram: il bot li registra all'avvio, quindi **non serve impostarli in BotFather** (se l'hai fatto, vengono sostituiti).
  ```
  e, dopo l'elenco, una riga: "Prima di salvare una ricerca il bot mostra un riepilogo da confermare; dopo il salvataggio puoi aggiungere il ritorno con un tocco."
- GUIDA_BOT_LIVE.md, sezione "Comandi e pulsanti": stessa nota sulla tastiera e su BotFather.
- Commit: `Documenta tastiera fissa, comandi automatici e riepilogo`.

---

## Verifica prima del deploy

1. `python -m pytest tests -v` verde (23).
2. Push su `main` → Railway. Nei log: `Avviato…` senza `registrazione comandi fallita`.
3. Su Telegram: la tastiera fissa compare al `/start`; il menù comandi (pulsante "/") mostra 7 voci all'invitato e 10 all'admin.
