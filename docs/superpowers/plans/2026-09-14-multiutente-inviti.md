# Bot multiutente con inviti — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettere a più persone di usare il bot Telegram, ognuna con le proprie ricerche e i propri avvisi, con inviti a codice gestiti dall'amministratore, revoca e tetto di ricerche per persona.

**Architecture:** Tutto resta nell'unico file `trovaunposto_live.py`. L'archivio `searches.json` passa a una struttura per utente (versione 2) con migrazione automatica dei dati esistenti; `seen.json` usa chiavi `"<user_id>:<ticket_id>"`. Le funzioni di dati (migrazione, inviti, limiti, revoca) sono pure e testate con pytest; gli handler Telegram le usano e si verificano a mano.

**Tech Stack:** Python 3.13, python-telegram-bot 21 (async, `ConversationHandler`, `JobQueue`), requests + BeautifulSoup per lo scraping, pytest per i test.

**Spec di riferimento:** `docs/superpowers/specs/2026-09-14-multiutente-inviti-design.md`

## Global Constraints

- Un solo file di codice: `trovaunposto_live.py`. Nessuno split in moduli.
- Formato ricerche per utente invariato (output di `make_search`).
- Admin = `TELEGRAM_CHAT_ID` (variabile `OWNER` nel codice). Sempre autorizzato.
- Env nuove: `MAX_SEARCHES_ADMIN` (default `5`), `MAX_SEARCHES_GUEST` (default `3`).
- Codici invito: 6 caratteri dall'alfabeto `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, validi 24 ore, monouso.
- Testi utente in italiano, esattamente come nella spec:
  - Non autorizzato: `Questo bot è privato. Se hai un codice d'invito scrivilo qui.`
  - Codice errato: `Codice non valido o scaduto.`
  - Limite: `Hai raggiunto il massimo di N ricerche attive. Rimuovine una da 📋 Le mie ricerche.`
  - Revoca: `Il tuo accesso al bot è stato revocato.`
- Tutti gli import del modulo devono restare privi di effetti collaterali (i test lo importano).
- Commit piccoli e frequenti, messaggi in italiano, con in coda la riga
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## Mappa dei file

| File | Ruolo |
|---|---|
| `trovaunposto_live.py` | Bot. Si aggiungono: costanti limiti, funzioni pure di store/seen/inviti/revoca, `get_user`, nuovi comandi, adattamento handler e `check_job`. |
| `tests/conftest.py` | Imposta le env di test prima dell'import e rende importabile il modulo. |
| `tests/test_multiuser.py` | Test pytest delle funzioni pure. |
| `requirements-dev.txt` | `pytest` per lo sviluppo locale. |
| `README.md`, `GUIDA_BOT_LIVE.md` | Documentazione comandi e variabili. |

**Ordine degli handler in `build_application`** (rilevante per i task 6–8): il wizard e i comandi restano nel gruppo 0; il nuovo `on_unauthorized_text` va nel gruppo 1.

---

### Task 0: Ambiente di sviluppo locale

**Files:**
- Create: `requirements-dev.txt`

- [ ] **Step 1: Installare le dipendenze del bot**

Run (PowerShell, nella cartella del progetto):
```powershell
pip install -r requirements.txt
```
Expected: installazione di `requests`, `beautifulsoup4`, `python-telegram-bot`, `tzdata` senza errori.

- [ ] **Step 2: Creare `requirements-dev.txt`**

```
pytest>=8
```

- [ ] **Step 3: Verificare che il modulo si importi**

Run:
```powershell
$env:TELEGRAM_BOT_TOKEN="test"; $env:TELEGRAM_CHAT_ID="1000"; python -c "import trovaunposto_live; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```powershell
git add requirements-dev.txt
git commit -m "Aggiunge requirements-dev con pytest`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: Store per utente con migrazione dal formato 1

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_multiuser.py`
- Modify: `trovaunposto_live.py` — sezione "Persistenza" (funzioni `load_store`, `save_store`, righe ~316-336)

**Interfaces:**
- Produces:
  - `STORE_VERSION = 2`
  - `empty_store() -> dict` → `{"version": 2, "users": {}, "invites": {}}`
  - `new_user(name: str, role: str, now: float) -> dict` → `{"name", "role", "searches": [], "paused": False, "joined": now}`
  - `migrate_store(old: dict, owner_id: str, now: float) -> dict` (pura, non tocca `old`)
  - `ensure_admin(store: dict, owner_id: str, name: str = "", now: float | None = None) -> dict` (record admin, creato se manca)
  - `load_store() -> dict` (già migrato, admin garantito, riscritto su disco se cambiato)

- [ ] **Step 1: Creare `tests/conftest.py`**

```python
import os
import sys

# Env minime PRIMA dell'import del modulo (che le legge a livello di modulo).
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1000")
os.environ.setdefault("DATA_DIR", os.path.join(os.path.dirname(__file__), "_data_tmp"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
```

- [ ] **Step 2: Scrivere i test (falliranno)**

`tests/test_multiuser.py`:
```python
import trovaunposto_live as bot

OWNER = "1000"
NOW = 1_757_800_000.0


def _search(name="Milano → Roma"):
    return bot.make_search("milano", "roma", "2026-10-01", "08:00", "12:00", 50) | {"name": name}


# --- Task 1: store -----------------------------------------------------------

def test_migrate_store_v1_moves_searches_under_admin():
    old = {"searches": [_search()], "paused": True}
    new = bot.migrate_store(old, OWNER, NOW)
    assert new["version"] == bot.STORE_VERSION
    assert new["invites"] == {}
    admin = new["users"][OWNER]
    assert admin["role"] == "admin"
    assert admin["paused"] is True
    assert admin["searches"] == old["searches"]
    assert admin["joined"] == NOW
    # non modifica l'input
    assert "users" not in old


def test_migrate_store_v2_is_unchanged():
    v2 = bot.empty_store()
    v2["users"]["42"] = bot.new_user("Anna", "guest", NOW)
    v2["invites"]["ABCDEF"] = {"created": NOW, "expires": NOW + 10}
    assert bot.migrate_store(v2, OWNER, NOW) == v2


def test_migrate_store_empty_input_gives_empty_store():
    assert bot.migrate_store({}, OWNER, NOW) == bot.empty_store()


def test_ensure_admin_creates_then_reuses():
    store = bot.empty_store()
    a = bot.ensure_admin(store, OWNER, "Matteo", NOW)
    assert store["users"][OWNER] is a
    assert a["role"] == "admin" and a["name"] == "Matteo"
    b = bot.ensure_admin(store, OWNER, "Altro", NOW + 1)
    assert b is a
    assert a["name"] == "Matteo"  # non sovrascrive il nome esistente
```

- [ ] **Step 3: Eseguire i test per vederli fallire**

Run:
```powershell
python -m pytest tests/test_multiuser.py -v
```
Expected: FAIL con `AttributeError: module 'trovaunposto_live' has no attribute 'migrate_store'` (e simili).

- [ ] **Step 4: Implementare nella sezione Persistenza**

Sostituire le attuali `load_store` e `save_store` (lasciando `_load`, `load_seen`, `save_seen`) con:

```python
STORE_VERSION = 2


def empty_store():
    return {"version": STORE_VERSION, "users": {}, "invites": {}}


def new_user(name, role, now):
    return {"name": name or "", "role": role, "searches": [], "paused": False, "joined": now}


def migrate_store(old, owner_id, now):
    """Converte l'archivio al formato per utente (versione 2). Pura: non tocca `old`.
    Formato 1: {"searches": [...], "paused": bool} -> tutto sotto l'admin."""
    if old.get("version") == STORE_VERSION and isinstance(old.get("users"), dict):
        new = json.loads(json.dumps(old))
        new.setdefault("invites", {})
        for u in new["users"].values():
            u.setdefault("searches", [])
            u.setdefault("paused", False)
            u.setdefault("name", "")
            u.setdefault("role", "guest")
            u.setdefault("joined", now)
        return new
    new = empty_store()
    if "searches" in old or "paused" in old:
        admin = new_user("", "admin", now)
        admin["searches"] = json.loads(json.dumps(old.get("searches", [])))
        admin["paused"] = bool(old.get("paused", False))
        new["users"][str(owner_id)] = admin
    return new


def ensure_admin(store, owner_id, name="", now=None):
    """Ritorna il record dell'admin, creandolo se manca. Non cambia un nome già presente."""
    uid = str(owner_id)
    user = store["users"].get(uid)
    if user is None:
        user = new_user(name, "admin", now if now is not None else dt.datetime.utcnow().timestamp())
        store["users"][uid] = user
    user["role"] = "admin"
    return user


def load_store():
    os.makedirs(DATA_DIR, exist_ok=True)
    raw = _load(SEARCHES_PATH, {})
    now = dt.datetime.utcnow().timestamp()
    store = migrate_store(raw, OWNER, now)
    ensure_admin(store, OWNER, now=now)
    if store != raw:
        save_store(store)
    return store


def save_store(store):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SEARCHES_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 5: Eseguire i test**

Run:
```powershell
python -m pytest tests/test_multiuser.py -v
```
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```powershell
git add tests/conftest.py tests/test_multiuser.py trovaunposto_live.py
git commit -m "Store per utente (v2) con migrazione automatica dal formato 1`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Chiavi `seen` per utente con migrazione

**Files:**
- Modify: `trovaunposto_live.py` — sezione Persistenza (`load_seen`, `save_seen`)
- Test: `tests/test_multiuser.py`

**Interfaces:**
- Produces:
  - `seen_key(user_id, ticket_id) -> str` → `"<user_id>:<ticket_id>"`
  - `migrate_seen(old: dict, owner_id: str) -> dict` (pura)
  - `load_seen() -> dict` (già migrato, riscritto se cambiato)

- [ ] **Step 1: Aggiungere i test**

Appendere a `tests/test_multiuser.py`:
```python
# --- Task 2: seen -------------------------------------------------------------

def test_seen_key():
    assert bot.seen_key("42", "987") == "42:987"
    assert bot.seen_key(42, 987) == "42:987"


def test_migrate_seen_prefixes_old_keys_with_owner():
    old = {"111": 1.0, "222": 2.0, "42:333": 3.0}
    new = bot.migrate_seen(old, OWNER)
    assert new == {"1000:111": 1.0, "1000:222": 2.0, "42:333": 3.0}
    assert "111" in old  # non modifica l'input
```

- [ ] **Step 2: Eseguire per vederli fallire**

Run: `python -m pytest tests/test_multiuser.py -v -k seen`
Expected: FAIL con `AttributeError ... 'seen_key'`.

- [ ] **Step 3: Implementare**

Sostituire `load_seen` con:
```python
def seen_key(user_id, ticket_id):
    return f"{user_id}:{ticket_id}"


def migrate_seen(old, owner_id):
    """Le chiavi vecchie (solo id biglietto) vengono attribuite all'admin."""
    out = {}
    for k, v in old.items():
        out[k if ":" in k else seen_key(owner_id, k)] = v
    return out


def load_seen():
    raw = _load(SEEN_PATH, {})
    seen = migrate_seen(raw, OWNER)
    if seen != raw:
        seen = save_seen(seen)
    return seen
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/test_multiuser.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_multiuser.py trovaunposto_live.py
git commit -m "Memoria biglietti visti per utente (chiave utente:biglietto) con migrazione`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Inviti — creazione, scadenza, riscatto

**Files:**
- Modify: `trovaunposto_live.py` — import `secrets`; nuova sottosezione "Inviti" dopo la Persistenza
- Test: `tests/test_multiuser.py`

**Interfaces:**
- Produces:
  - `INVITE_ALPHABET`, `INVITE_TTL_SECONDS = 24 * 3600`, `INVITE_CODE_RE` (regex `^[A-Z2-9]{6}$`)
  - `purge_invites(store, now) -> None` (rimuove gli scaduti in place)
  - `new_invite(store, now) -> str` (aggiunge e ritorna il codice)
  - `redeem_invite(store, code, user_id, name, now) -> bool`

- [ ] **Step 1: Aggiungere i test**

```python
# --- Task 3: inviti -----------------------------------------------------------

def test_new_invite_code_shape_and_expiry():
    store = bot.empty_store()
    code = bot.new_invite(store, NOW)
    assert len(code) == 6 and all(c in bot.INVITE_ALPHABET for c in code)
    assert bot.INVITE_CODE_RE.match(code)
    inv = store["invites"][code]
    assert inv["created"] == NOW
    assert inv["expires"] == NOW + bot.INVITE_TTL_SECONDS


def test_redeem_invite_success_is_single_use():
    store = bot.empty_store()
    code = bot.new_invite(store, NOW)
    assert bot.redeem_invite(store, code, "42", "Anna", NOW + 60) is True
    user = store["users"]["42"]
    assert user["role"] == "guest" and user["name"] == "Anna"
    assert user["searches"] == [] and user["paused"] is False
    assert user["joined"] == NOW + 60
    assert code not in store["invites"]
    # seconda volta: rifiutato
    assert bot.redeem_invite(store, code, "43", "Bruno", NOW + 61) is False
    assert "43" not in store["users"]


def test_redeem_invite_expired_or_unknown():
    store = bot.empty_store()
    code = bot.new_invite(store, NOW)
    late = NOW + bot.INVITE_TTL_SECONDS + 1
    assert bot.redeem_invite(store, code, "42", "Anna", late) is False
    assert code not in store["invites"]  # scaduto e ripulito
    assert bot.redeem_invite(store, "ZZZZZZ", "42", "Anna", NOW) is False


def test_redeem_invite_is_case_insensitive():
    store = bot.empty_store()
    code = bot.new_invite(store, NOW)
    assert bot.redeem_invite(store, code.lower(), "42", "Anna", NOW) is True


def test_purge_invites_keeps_only_valid():
    store = bot.empty_store()
    store["invites"] = {"AAAAAA": {"created": 0, "expires": NOW - 1},
                        "BBBBBB": {"created": 0, "expires": NOW + 1}}
    bot.purge_invites(store, NOW)
    assert list(store["invites"]) == ["BBBBBB"]
```

- [ ] **Step 2: Eseguire per vederli fallire**

Run: `python -m pytest tests/test_multiuser.py -v -k invite`
Expected: FAIL con `AttributeError ... 'new_invite'`.

- [ ] **Step 3: Implementare**

In testa al file aggiungere `import secrets` tra gli import standard (dopo `import re`).

Dopo `save_seen` aggiungere:
```python
# ---------------------------------------------------------------------------
# Inviti (codici monouso generati dall'admin)
# ---------------------------------------------------------------------------
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # senza 0/O, 1/I
INVITE_TTL_SECONDS = 24 * 3600
INVITE_CODE_RE = re.compile(r"^[A-Z2-9]{6}$")


def purge_invites(store, now):
    store["invites"] = {c: v for c, v in store.get("invites", {}).items()
                        if v.get("expires", 0) > now}


def new_invite(store, now):
    purge_invites(store, now)
    while True:
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(6))
        if code not in store["invites"]:
            break
    store["invites"][code] = {"created": now, "expires": now + INVITE_TTL_SECONDS}
    return code


def redeem_invite(store, code, user_id, name, now):
    """Consuma il codice e registra l'utente come invitato. False se non valido/scaduto."""
    purge_invites(store, now)
    code = (code or "").strip().upper()
    if code not in store["invites"]:
        return False
    del store["invites"][code]
    store["users"][str(user_id)] = new_user(name, "guest", now)
    return True
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/test_multiuser.py -v`
Expected: 11 PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_multiuser.py trovaunposto_live.py
git commit -m "Inviti con codice monouso valido 24 ore`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Limiti per persona e revoca

**Files:**
- Modify: `trovaunposto_live.py` — sezione Configurazione (dopo `CHECK_INTERVAL`) e sottosezione Inviti
- Test: `tests/test_multiuser.py`

**Interfaces:**
- Produces:
  - `MAX_SEARCHES_ADMIN`, `MAX_SEARCHES_GUEST` (int da env, default 5 e 3)
  - `search_limit(user) -> int`
  - `is_admin(user) -> bool`
  - `revoke_user(store, seen, user_id) -> None` (in place: rimuove l'utente e le sue chiavi `seen`)

- [ ] **Step 1: Aggiungere i test**

```python
# --- Task 4: limiti e revoca --------------------------------------------------

def test_search_limit_by_role(monkeypatch):
    assert bot.search_limit({"role": "admin"}) == 5
    assert bot.search_limit({"role": "guest"}) == 3
    monkeypatch.setattr(bot, "MAX_SEARCHES_ADMIN", 9)
    monkeypatch.setattr(bot, "MAX_SEARCHES_GUEST", 1)
    assert bot.search_limit({"role": "admin"}) == 9
    assert bot.search_limit({"role": "guest"}) == 1


def test_is_admin():
    assert bot.is_admin({"role": "admin"}) is True
    assert bot.is_admin({"role": "guest"}) is False


def test_revoke_user_removes_only_that_user():
    store = bot.empty_store()
    bot.ensure_admin(store, OWNER, "Matteo", NOW)
    store["users"]["42"] = bot.new_user("Anna", "guest", NOW)
    store["users"]["42"]["searches"].append(_search())
    seen = {"1000:1": 1.0, "42:2": 2.0, "42:3": 3.0, "420:4": 4.0}
    bot.revoke_user(store, seen, "42")
    assert "42" not in store["users"]
    assert OWNER in store["users"]
    assert seen == {"1000:1": 1.0, "420:4": 4.0}
```

- [ ] **Step 2: Eseguire per vederli fallire**

Run: `python -m pytest tests/test_multiuser.py -v -k "limit or admin or revoke"`
Expected: FAIL con `AttributeError`.

- [ ] **Step 3: Implementare**

Nella sezione Configurazione, subito dopo `CHECK_INTERVAL = ...`:
```python
# Tetto di ricerche attive per persona (ogni ricerca = 1 richiesta al sito al minuto)
MAX_SEARCHES_ADMIN = int(os.environ.get("MAX_SEARCHES_ADMIN", "5"))
MAX_SEARCHES_GUEST = int(os.environ.get("MAX_SEARCHES_GUEST", "3"))
```

Dopo `redeem_invite`:
```python
def is_admin(user):
    return bool(user) and user.get("role") == "admin"


def search_limit(user):
    return MAX_SEARCHES_ADMIN if is_admin(user) else MAX_SEARCHES_GUEST


def revoke_user(store, seen, user_id):
    """Rimuove l'utente, le sue ricerche e la sua memoria dei biglietti visti (in place)."""
    uid = str(user_id)
    store["users"].pop(uid, None)
    prefix = uid + ":"
    for k in [k for k in seen if k.startswith(prefix)]:
        del seen[k]
```

- [ ] **Step 4: Eseguire i test**

Run: `python -m pytest tests/test_multiuser.py -v`
Expected: 14 PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_multiuser.py trovaunposto_live.py
git commit -m "Limiti di ricerche per ruolo e revoca utente`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Autorizzazione per utente e adattamento dei comandi esistenti

**Files:**
- Modify: `trovaunposto_live.py` — `is_owner` → `get_user`; `cmd_start`, `show_list`, `cmd_list`, `cmd_pause`, `cmd_resume`, `cmd_status`, `cmd_clear`, `cmd_debug`, `on_menu`, `wiz_start_add`, `wiz_start`, `_finish`; nuovo `on_unauthorized_text`, `_handle_invite_code`; registrazione in `build_application`

**Interfaces:**
- Consumes: `ensure_admin`, `save_store`, `redeem_invite`, `INVITE_CODE_RE`, `search_limit`, `is_admin`, `seen_key`
- Produces:
  - `get_user(update, store) -> dict | None`
  - `PRIVATE_MSG`, `INVALID_CODE_MSG`, `limit_msg(n) -> str`
  - `async _handle_invite_code(update, context, code)` (risponde sempre; True se il codice è stato riscattato)
  - `async on_unauthorized_text(update, context)`

Nessun test automatico: gli handler si verificano a mano allo Step 5.

- [ ] **Step 1: Sostituire `is_owner` con `get_user` e i messaggi**

Sostituire la funzione `is_owner` (sezione "Helpers Telegram") con:
```python
PRIVATE_MSG = "Questo bot è privato. Se hai un codice d'invito scrivilo qui."
INVALID_CODE_MSG = "Codice non valido o scaduto."


def limit_msg(n):
    return (f"Hai raggiunto il massimo di {n} ricerche attive. "
            "Rimuovine una da 📋 <b>Le mie ricerche</b>.")


def get_user(update: Update, store):
    """Record dell'utente che scrive, o None se non autorizzato.
    L'admin (OWNER) è sempre autorizzato; il nome viene aggiornato se cambia."""
    u = update.effective_user
    if u is None:
        return None
    uid = str(u.id)
    name = u.full_name or u.username or uid
    changed = False
    if uid == str(OWNER):
        if uid not in store["users"]:
            changed = True
        user = ensure_admin(store, OWNER, name)
    else:
        user = store["users"].get(uid)
        if user is None:
            return None
    if name and user.get("name") != name:
        user["name"] = name
        changed = True
    if changed:
        save_store(store)
    return user
```

- [ ] **Step 2: Adattare i comandi base**

Sostituire `cmd_start`, `show_list`, `cmd_list`, `cmd_pause`, `cmd_resume`, `cmd_status` con:
```python
async def _handle_invite_code(update, context, code):
    """Prova a riscattare un codice per un utente NON autorizzato. Risponde sempre."""
    store = context.application.bot_data["store"]
    u = update.effective_user
    name = u.full_name or u.username or str(u.id)
    now = dt.datetime.utcnow().timestamp()
    if not redeem_invite(store, code, u.id, name, now):
        await update.effective_message.reply_text(INVALID_CODE_MSG)
        return False
    save_store(store)
    await update.effective_message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(False))
    try:
        await context.bot.send_message(
            chat_id=OWNER, parse_mode=ParseMode.HTML,
            text=f"👤 <b>{esc(name)}</b> è entrato con il codice <code>{esc(code.upper())}</code>.")
    except Exception as e:  # noqa: BLE001
        log.warning("avviso admin fallito: %s", e)
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        code = (context.args[0] if context.args else "").strip().upper()
        if code:
            await _handle_invite_code(update, context, code)
        else:
            await update.effective_message.reply_text(PRIVATE_MSG)
        return
    await update.effective_message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(user["paused"])
    )


async def on_unauthorized_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gruppo 1: agisce solo per chi NON è autorizzato (codice d'invito o messaggio 'privato')."""
    store = context.application.bot_data["store"]
    if get_user(update, store) is not None:
        return
    text = (update.message.text or "").strip().upper()
    if INVITE_CODE_RE.match(text):
        await _handle_invite_code(update, context, text)
    else:
        await update.effective_message.reply_text(PRIVATE_MSG)


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    searches = user["searches"]
    if not searches:
        text = "Non hai ancora ricerche attive.\nPremi ➕ <b>Nuova ricerca</b> per crearne una."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Nuova ricerca", callback_data="new")]])
    else:
        text = (f"📋 <b>Le tue ricerche attive</b> ({len(searches)}/{search_limit(user)}):\n\n"
                + "\n".join(f"{i+1}. {esc(search_summary(s))}" for i, s in enumerate(searches)))
        rows = []
        for i in range(len(searches)):
            rows.append([
                InlineKeyboardButton(f"🔎 Disponibili #{i+1}", callback_data=f"avail:{i}"),
                InlineKeyboardButton(f"🗑 Rimuovi #{i+1}", callback_data=f"del:{i}"),
            ])
        rows.append([InlineKeyboardButton("➕ Nuova ricerca", callback_data="new")])
        kb = InlineKeyboardMarkup(rows)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user(update, context.application.bot_data["store"]) is None:
        return
    await show_list(update, context)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return
    user["paused"] = True
    save_store(store)
    await update.effective_message.reply_text("⏸️ Notifiche sospese. Usa /riprendi per riattivarle.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return
    user["paused"] = False
    save_store(store)
    await update.effective_message.reply_text("▶️ Notifiche riattivate.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return
    stato = "in pausa ⏸️" if user["paused"] else "attivo ✅"
    lines = [f"Stato: <b>{stato}</b>",
             f"Ricerche attive: {len(user['searches'])}/{search_limit(user)}",
             f"Controllo ogni {CHECK_INTERVAL}s."]
    if is_admin(user):
        total = sum(len(u["searches"]) for u in store["users"].values())
        lines.append(f"Utenti: {len(store['users'])} · Ricerche totali: {total}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
```

In `cmd_clear` sostituire le prime due righe del corpo con:
```python
    if get_user(update, context.application.bot_data["store"]) is None:
        return
```

In `cmd_debug` sostituire il controllo con:
```python
    if not is_admin(get_user(update, context.application.bot_data["store"])):
        return
```

- [ ] **Step 3: Adattare `on_menu`**

Sostituire l'inizio di `on_menu` fino a `elif data.startswith("avail:")` compreso, così:
```python
async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return
    data = q.data
    if data == "list":
        await show_list(update, context, edit=True)
    elif data == "pause":
        user["paused"] = True
        save_store(store)
        await q.edit_message_text("⏸️ Notifiche sospese.", reply_markup=main_menu_kb(True))
    elif data == "resume":
        user["paused"] = False
        save_store(store)
        await q.edit_message_text("▶️ Notifiche riattivate.", reply_markup=main_menu_kb(False))
    elif data.startswith("del:"):
        idx = int(data.split(":")[1])
        if 0 <= idx < len(user["searches"]):
            removed = user["searches"].pop(idx)
            save_store(store)
            await q.edit_message_text(f"🗑 Rimossa: {esc(search_summary(removed))}")
        await show_list(update, context)
    elif data.startswith("avail:"):
        idx = int(data.split(":")[1])
        if 0 <= idx < len(user["searches"]):
            search = user["searches"][idx]
            await q.message.reply_text("🔎 Controllo i biglietti disponibili ora…")
            try:
                matches = await asyncio.to_thread(lambda s=search: find_matches(s))
                await q.message.reply_text(
                    render_matches(search, matches),
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            except Exception:
                await q.message.reply_text("Non sono riuscito a controllare ora, riprova tra poco.")
```

- [ ] **Step 4: Adattare il wizard (avvio con limite, salvataggio per utente)**

Sostituire `wiz_start_add` e l'inizio di `wiz_start`:
```python
async def wiz_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        if update.callback_query:
            await update.callback_query.answer()
        return ConversationHandler.END
    limit = search_limit(user)
    if len(user["searches"]) >= limit:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(limit_msg(limit), parse_mode=ParseMode.HTML)
        else:
            await update.effective_message.reply_text(limit_msg(limit), parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    context.user_data["mode"] = "add"
    return await wiz_start(update, context)


async def wiz_start_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "search"
    return await wiz_start(update, context)


async def wiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user(update, context.application.bot_data["store"]) is None:
        if update.callback_query:
            await update.callback_query.answer()
        return ConversationHandler.END
    context.user_data["draft"] = {}
    # ... resto invariato (testo "Da dove parti?" e tastiera città)
```

In `_finish`, sostituire il blocco `# mode == "add"` fino a `await send(f"✅ ...")` con:
```python
    # mode == "add": salva la ricerca dell'utente e attiva gli avvisi.
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    uid = str(update.effective_user.id)
    limit = search_limit(user)
    if len(user["searches"]) >= limit:
        await send(limit_msg(limit))
        context.user_data.pop("draft", None)
        context.user_data.pop("mode", None)
        return ConversationHandler.END
    user["searches"].append(search)
    save_store(store)
    await send(f"✅ <b>Ricerca creata!</b>\n{esc(search_summary(search))}")
```
e, nel blocco successivo che registra i biglietti già presenti, sostituire `seen[card["id"]] = now` con `seen[seen_key(uid, card["id"])] = now`.

- [ ] **Step 5: Registrare `on_unauthorized_text` nel gruppo 1**

In `build_application`, dopo la riga `app.add_handler(CallbackQueryHandler(on_menu, ...))`:
```python
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_unauthorized_text), group=1)
```

- [ ] **Step 6: Verifica**

Run: `python -m pytest tests -v` → Expected: 14 PASS (nessuna regressione all'import).

Run: `python -c "import ast,sys; ast.parse(open('trovaunposto_live.py',encoding='utf-8').read()); print('syntax ok')"` → Expected: `syntax ok`.

Prova manuale (bot in locale con token e id reali, `DATA_DIR` locale contenente una copia del vecchio `searches.json`):
1. Avvio: il file viene riscritto in versione 2 con le ricerche sotto il tuo ID. `/lista` mostra le ricerche di prima con `(n/5)`.
2. Da un secondo account Telegram: scrivere "ciao" al bot → risposta `Questo bot è privato. Se hai un codice d'invito scrivilo qui.`
3. `/stato` dal tuo account → mostra `Utenti: 1`.

- [ ] **Step 7: Commit**

```powershell
git add trovaunposto_live.py
git commit -m "Autorizzazione per utente: comandi, menu e wizard lavorano sui dati di chi scrive`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Comandi admin `/invita` e `/utenti` con revoca

**Files:**
- Modify: `trovaunposto_live.py` — nuova sottosezione "Comandi admin" prima di `on_menu`; estensione di `on_menu` e del pattern in `build_application`

**Interfaces:**
- Consumes: `get_user`, `is_admin`, `new_invite`, `purge_invites`, `revoke_user`, `save_store`, `save_seen`
- Produces:
  - `users_view(store) -> (text: str, kb: InlineKeyboardMarkup | None)`
  - `async cmd_invite`, `async cmd_users`
  - callback `revoke:<id>`, `revoke_ok:<id>`, `revoke_no`

- [ ] **Step 1: Aggiungere i comandi admin**

Prima della sezione "Callback dei pulsanti del menu":
```python
# ---------------------------------------------------------------------------
# Comandi admin: inviti e gestione utenti
# ---------------------------------------------------------------------------
async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    if not is_admin(get_user(update, store)):
        return
    code = new_invite(store, dt.datetime.utcnow().timestamp())
    save_store(store)
    link = f"https://t.me/{context.bot.username}?start={code}"
    await update.effective_message.reply_text(
        f"🔗 <b>Invito creato</b>\nCodice: <code>{code}</code>\n{link}\n\n"
        "Inoltra questo link alla persona da invitare. Vale 24 ore, una sola volta.",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def users_view(store):
    guests = [(uid, u) for uid, u in store["users"].items() if not is_admin(u)]
    if not guests:
        return ("Nessun invitato. Usa /invita per generare un codice.", None)
    lines = ["👥 <b>Utenti invitati</b>\n"]
    rows = []
    for uid, u in guests:
        joined = dt.datetime.fromtimestamp(u.get("joined", 0), TZ).strftime("%d/%m/%Y")
        lines.append(f"• <b>{esc(u.get('name') or uid)}</b> — {len(u['searches'])} ricerche · dal {joined}")
        rows.append([InlineKeyboardButton(f"🗑 Revoca {u.get('name') or uid}", callback_data=f"revoke:{uid}")])
    return ("\n".join(lines), InlineKeyboardMarkup(rows))


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    if not is_admin(get_user(update, store)):
        return
    purge_invites(store, dt.datetime.utcnow().timestamp())
    text, kb = users_view(store)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
```

- [ ] **Step 2: Estendere `on_menu` con la revoca**

In fondo a `on_menu`, dopo il ramo `avail:`:
```python
    elif data.startswith("revoke:") or data.startswith("revoke_ok:") or data == "revoke_no":
        if not is_admin(user):
            return
        if data == "revoke_no":
            text, kb = users_view(store)
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        uid = data.split(":")[1]
        target = store["users"].get(uid)
        if target is None or is_admin(target):
            text, kb = users_view(store)
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        if data.startswith("revoke:"):
            n = len(target["searches"])
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Sì, revoca", callback_data=f"revoke_ok:{uid}"),
                InlineKeyboardButton("❌ Annulla", callback_data="revoke_no"),
            ]])
            await q.edit_message_text(
                f"Revocare l'accesso a <b>{esc(target.get('name') or uid)}</b>? "
                f"Le sue {n} ricerche verranno cancellate.",
                parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        # revoke_ok
        name = target.get("name") or uid
        seen = context.application.bot_data["seen"]
        revoke_user(store, seen, uid)
        save_store(store)
        context.application.bot_data["seen"] = save_seen(seen)
        try:
            await context.bot.send_message(chat_id=uid, text="Il tuo accesso al bot è stato revocato.")
        except Exception as e:  # noqa: BLE001
            log.warning("avviso revoca fallito: %s", e)
        text, kb = users_view(store)
        await q.edit_message_text(f"🗑 Accesso revocato a <b>{esc(name)}</b>.\n\n{text}",
                                  parse_mode=ParseMode.HTML, reply_markup=kb)
```

- [ ] **Step 3: Registrare gli handler**

In `build_application`:
```python
    app.add_handler(CommandHandler("invita", cmd_invite))
    app.add_handler(CommandHandler("utenti", cmd_users))
    app.add_handler(CallbackQueryHandler(
        on_menu,
        pattern=r"^(list|pause|resume|del:\d+|avail:\d+|revoke:\d+|revoke_ok:\d+|revoke_no)$"))
```
(sostituisce la riga esistente del `CallbackQueryHandler(on_menu, ...)`).

- [ ] **Step 4: Verifica**

Run: `python -m pytest tests -v` → Expected: 14 PASS.

Prova manuale:
1. `/invita` → codice e link `https://t.me/<bot>?start=CODICE`.
2. Dal secondo account, aprire il link e premere Avvia → riceve il benvenuto con il menù; tu ricevi `👤 <nome> è entrato con il codice ...`.
3. Riaprire lo stesso link da un terzo account (o riscrivere il codice) → `Codice non valido o scaduto.`
4. Dal secondo account creare 3 ricerche; alla quarta → messaggio di limite. `/lista` mostra `(3/3)`. Le tue ricerche non compaiono nella sua lista e viceversa.
5. `/utenti` → riga con nome, ricerche, data; premere Revoca → conferma → Sì. Il secondo account riceve `Il tuo accesso al bot è stato revocato.` e se scrive di nuovo riceve il messaggio "privato".

- [ ] **Step 5: Commit**

```powershell
git add trovaunposto_live.py
git commit -m "Comandi admin /invita e /utenti con revoca a conferma`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Controllo periodico e avvio per utente

**Files:**
- Modify: `trovaunposto_live.py` — `check_job`, `on_startup`

**Interfaces:**
- Consumes: `seen_key`, `save_seen`, `load_store`, `load_seen`
- Produces:
  - `iter_searches(store, only_active: bool) -> iterator[(uid, search)]`
  - `async broadcast(bot, text)`

- [ ] **Step 1: Helper di iterazione e broadcast**

Prima di `check_job`:
```python
def iter_searches(store, only_active=True):
    """Coppie (user_id, search). Con only_active salta gli utenti in pausa."""
    for uid, user in store["users"].items():
        if only_active and user.get("paused"):
            continue
        for s in user.get("searches", []):
            yield uid, s


async def broadcast(bot, store, text):
    """Manda lo stesso messaggio a tutti gli utenti (errori di invio solo loggati)."""
    for uid in list(store["users"]):
        try:
            await bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML)
        except Exception as e:  # noqa: BLE001
            log.warning("broadcast a %s fallito: %s", uid, e)
```

- [ ] **Step 2: Riscrivere `check_job`**

```python
async def check_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    store = app.bot_data["store"]
    todo = list(iter_searches(store, only_active=True))
    if not todo:
        return
    now = dt.datetime.utcnow().timestamp()
    # Auto-rallentamento: se il sito è in difficoltà, salta i controlli finché non scade.
    if now < app.bot_data.get("backoff_until", 0):
        return
    seen = app.bot_data["seen"]
    changed = False
    attempted = failures = 0
    for uid, search in todo:
        attempted += 1
        try:
            matches = await asyncio.to_thread(lambda s=search: find_matches(s))
        except Exception as e:  # noqa: BLE001
            failures += 1
            log.warning("controllo fallito per %s (%s): %s", search.get("name"), uid, e)
            continue
        for card, m in matches:
            key = seen_key(uid, card["id"])
            if key in seen:
                continue
            seen[key] = now
            changed = True
            try:
                await context.bot.send_message(
                    chat_id=uid, text=notify_text(search, card, m),
                    parse_mode=ParseMode.HTML, disable_web_page_preview=False)
            except Exception as e:  # noqa: BLE001
                log.warning("invio notifica a %s fallito: %s", uid, e)
    if changed:
        app.bot_data["seen"] = save_seen(seen)

    # Stato del sito: se TUTTE le richieste falliscono = probabile blocco/irraggiungibile.
    if attempted and failures == attempted:
        app.bot_data["fail_streak"] = app.bot_data.get("fail_streak", 0) + 1
        if app.bot_data["fail_streak"] >= FAIL_THRESHOLD:
            app.bot_data["backoff_until"] = now + SLOW_SECONDS
            if not app.bot_data.get("slowed"):
                app.bot_data["slowed"] = True
                await broadcast(
                    context.bot, store,
                    "⚠️ Il sito non risponde da qualche minuto: ho <b>rallentato</b> i "
                    "controlli per non sovraccaricarlo. Ti avviso appena torna disponibile.")
    else:
        if app.bot_data.get("slowed"):
            app.bot_data["slowed"] = False
            await broadcast(context.bot, store,
                            "✅ Il sito risponde di nuovo: controlli ripristinati alla frequenza normale.")
        app.bot_data["fail_streak"] = 0
        app.bot_data["backoff_until"] = 0
```

- [ ] **Step 3: Adattare `on_startup`**

```python
async def on_startup(app: Application):
    app.bot_data["store"] = load_store()
    app.bot_data["seen"] = load_seen()
    store = app.bot_data["store"]
    all_searches = list(iter_searches(store, only_active=False))
    # primo avvio "pulito": se non ho memoria, registro i biglietti attuali in
    # silenzio così non parte una valanga di notifiche al primo giro.
    if not app.bot_data["seen"] and all_searches:
        seen = {}
        now = dt.datetime.utcnow().timestamp()
        for uid, search in all_searches:
            try:
                matches = await asyncio.to_thread(lambda s=search: find_matches(s))
            except Exception:
                continue
            for c, _m in matches:
                seen[seen_key(uid, c["id"])] = now
        app.bot_data["seen"] = save_seen(seen)
    log.info("Avviato. Utenti: %d, ricerche: %d, intervallo %ds.",
             len(store["users"]), len(all_searches), CHECK_INTERVAL)
    try:
        admin = ensure_admin(store, OWNER)
        await app.bot.send_message(chat_id=OWNER, text=WELCOME, parse_mode=ParseMode.HTML,
                                   reply_markup=main_menu_kb(admin["paused"]))
    except Exception as e:  # noqa: BLE001
        log.warning("Impossibile inviare il messaggio di avvio: %s", e)
```

- [ ] **Step 4: Verifica**

Run: `python -m pytest tests -v` → Expected: 14 PASS.

Prova manuale: con due utenti che seguono la stessa tratta, la comparsa di un biglietto nuovo produce un avviso a ciascuno; con un utente in pausa, solo l'altro riceve. Il log all'avvio riporta `Utenti: 2, ricerche: N`.

- [ ] **Step 5: Commit**

```powershell
git add trovaunposto_live.py
git commit -m "Controllo periodico per utente: avvisi alla persona giusta, stato sito a tutti`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Documentazione

**Files:**
- Modify: `trovaunposto_live.py` — docstring iniziale (righe 3-18)
- Modify: `README.md` — sezioni "Comandi e pulsanti del bot" e "Configurazione"
- Modify: `GUIDA_BOT_LIVE.md` — variabili opzionali

- [ ] **Step 1: Docstring del modulo**

Sostituire il blocco "Variabili d'ambiente" della docstring con:
```
Variabili d'ambiente:
  TELEGRAM_BOT_TOKEN   (obbligatoria)
  TELEGRAM_CHAT_ID     (obbligatoria: il tuo id numerico; sei l'amministratore,
                        puoi invitare altre persone con /invita)
  CHECK_INTERVAL       (opzionale, secondi tra un controllo e l'altro, default 60)
  DATA_DIR             (opzionale, cartella per i dati persistenti, default ./data)
  MAX_SEARCHES_ADMIN   (opzionale, ricerche attive massime per l'admin, default 5)
  MAX_SEARCHES_GUEST   (opzionale, ricerche attive massime per ogni invitato, default 3)
```

- [ ] **Step 2: README**

Nella sezione "Comandi e pulsanti del bot", dopo la riga di `/aiuto`, aggiungere:
```markdown
- `/invita` (solo amministratore): genera un codice d'invito monouso valido 24 ore e un link da inoltrare.
- `/utenti` (solo amministratore): elenca le persone invitate e permette di revocare l'accesso (le loro ricerche vengono cancellate).

Ogni persona ha le **proprie** ricerche, la propria pausa e riceve solo i propri avvisi.
Tetto di ricerche attive: 5 per l'amministratore, 3 per ogni invitato (modificabile, vedi sotto).
```

Nella tabella "Configurazione" aggiungere le righe:
```markdown
| `MAX_SEARCHES_ADMIN` | opzionale | Ricerche attive massime per l'amministratore (default 5). |
| `MAX_SEARCHES_GUEST` | opzionale | Ricerche attive massime per ogni invitato (default 3). |
```
e cambiare la descrizione di `TELEGRAM_CHAT_ID` in: `Il tuo id numerico: sei l'amministratore (puoi invitare altri con /invita).`

Nella tabella "File del progetto" aggiungere:
```markdown
| `tests/` | Test automatici (`pip install -r requirements-dev.txt` e poi `python -m pytest`). |
```

- [ ] **Step 3: GUIDA_BOT_LIVE.md**

Nel punto in cui si elencano le variabili su Railway, aggiungere dopo `CHECK_INTERVAL`:
```markdown
- `MAX_SEARCHES_ADMIN` (facoltativa): ricerche attive massime per te, default 5.
- `MAX_SEARCHES_GUEST` (facoltativa): ricerche attive massime per ogni persona invitata, default 3.
```
e una breve nota: "Per condividere il bot con qualcuno: scrivi `/invita` al bot, inoltra il link che ti risponde. Con `/utenti` vedi e revochi gli accessi."

- [ ] **Step 4: Verifica finale e commit**

Run: `python -m pytest tests -v` → Expected: 14 PASS.

```powershell
git add trovaunposto_live.py README.md GUIDA_BOT_LIVE.md
git commit -m "Documenta inviti, utenti e limiti per persona`n`nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Verifica end-to-end prima del deploy

1. `python -m pytest tests -v` verde.
2. Bot in locale con una copia dei dati di produzione (`DATA_DIR` locale): avvio senza errori, ricerche esistenti conservate sotto l'admin, `seen.json` riscritto con prefisso.
3. Flusso completo con un secondo account: invito → ingresso → ricerche separate → limite → revoca.
4. Push su GitHub: Railway ridistribuisce; il volume `/data` viene migrato al primo avvio.
