#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trovaunposto LIVE — bot Telegram sempre attivo.

Differenze rispetto alla versione a 5 minuti:
- Input GUIDATO a bottoni: niente formati da ricordare. Premi "Nuova ricerca" e
  il bot ti chiede partenza, arrivo, giorno, fascia oraria e prezzo, un passo alla
  volta, con pulsanti pronti.
- Controllo dei biglietti ogni ~60 secondi (quasi in tempo reale).

Variabili d'ambiente:
  TELEGRAM_BOT_TOKEN   (obbligatoria)
  TELEGRAM_CHAT_ID     (obbligatoria: il tuo id numerico; sei l'amministratore,
                        puoi invitare altre persone con /invita)
  CHECK_INTERVAL       (opzionale, secondi tra un controllo e l'altro, default 60)
  DATA_DIR             (opzionale, cartella per i dati persistenti, default ./data)
  MAX_SEARCHES_ADMIN   (opzionale, ricerche attive massime per l'admin, default 5)
  MAX_SEARCHES_GUEST   (opzionale, ricerche attive massime per ogni invitato, default 3)
"""

import asyncio
import datetime as dt
import json
import logging
import os
import re
import secrets
import urllib.parse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("trovaunposto")

# Evita che il token del bot finisca nei log: le librerie HTTP, a livello INFO,
# stamperebbero l'URL completo delle chiamate a Telegram (token incluso).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER = os.environ.get("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))
# Tetto di ricerche attive per persona (ogni ricerca = 1 richiesta al sito al minuto)
MAX_SEARCHES_ADMIN = int(os.environ.get("MAX_SEARCHES_ADMIN", "5"))
MAX_SEARCHES_GUEST = int(os.environ.get("MAX_SEARCHES_GUEST", "3"))
# Auto-rallentamento quando il sito è in difficoltà
FAIL_THRESHOLD = 3      # cicli consecutivi tutti falliti prima di rallentare
SLOW_SECONDS = 600      # pausa tra i tentativi quando il sito non risponde (10 min)
DATA_DIR = os.environ.get("DATA_DIR", "./data")
SEARCHES_PATH = os.path.join(DATA_DIR, "searches.json")
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")

try:
    TZ = ZoneInfo("Europe/Rome")
except Exception:  # se sul server mancano i dati dei fusi orari
    TZ = dt.timezone.utc
POPULAR_CITIES = ["Milano", "Roma", "Napoli", "Torino", "Firenze", "Bologna", "Venezia", "Bari"]
SEEN_RETENTION_DAYS = 45

# ---------------------------------------------------------------------------
# Scraping (logica riutilizzata e già collaudata)
# ---------------------------------------------------------------------------
BASE = "https://trovaunposto.it"
SEARCH_PATH = "/trains/searchTrainTicket"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TICKET_RE = re.compile(r"/payments/buyTrainTicket/(\d+)")
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*€")
SUMMARY_TIMES_RE = re.compile(r"(\d{1,2}:\d{2})\s*>\s*(\d{1,2}:\d{2})")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")
SELLER_RE = re.compile(r"Biglietto di\s+([^\n]+)")
TERMINATORS = [
    "Cambio nominativo", "Informativa sui biglietti", "Seleziona biglietto",
    "Biglietto di", "Garanzia Trovaunposto", "Ogni transazione",
    "Scopri di più", "Scopri di piu",
]


def norm_time(t):
    try:
        h, m = t.split(":")
        return f"{int(h):02d}:{int(m):02d}"
    except Exception:
        return t


def parse_price(s):
    if s is None:
        return None
    s = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def to_ddmmyyyy(value):
    if not value:
        return None
    value = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return value


def city_key(city):
    return city.strip().upper().split("(")[0].strip()


def to_station(city):
    c = city.strip()
    if "(" in c:
        return c.upper()
    return f"{c.upper()}(TUTTE LE STAZIONI)"


def fetch(url, retries=3, timeout=30):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    }
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last = e
            import time as _t
            _t.sleep(2 * attempt)
    raise RuntimeError(f"fetch fallito {url}: {last}")


def build_search_url(search):
    if search.get("search_url"):
        return search["search_url"]
    dep = search.get("departure", "")
    arr = search.get("arrival", "")
    params = {
        "departure": dep, "departure_id": search.get("departure_id", dep),
        "arrival": arr, "arrival_id": search.get("arrival_id", arr),
        "date": search.get("date", "") or "",
    }
    return BASE + SEARCH_PATH + "?" + urllib.parse.urlencode(params)


def card_root_for(link):
    node = link
    while node.parent is not None:
        parent = node.parent
        if len(TICKET_RE.findall(str(parent))) == 1:
            node = parent
        else:
            break
    return node


def cut_at_terminators(text):
    cut = len(text)
    for term in TERMINATORS:
        i = text.find(term)
        if i != -1:
            cut = min(cut, i)
    return text[:cut]


def build_segment(seg_lines, kind):
    seg_text = cut_at_terminators("\n".join(seg_lines))
    date_m = DATE_RE.search(seg_text)
    date = date_m.group(1) if date_m else None
    stops = []
    times = list(TIME_RE.finditer(seg_text))
    for k, m in enumerate(times):
        start = m.end()
        end = times[k + 1].start() if k + 1 < len(times) else len(seg_text)
        station = re.sub(r"\s+", " ", seg_text[start:end]).strip(" -–·\n\t")
        if station:
            stops.append((norm_time(m.group(1)), station))
    return {"kind": kind, "date": date, "stops": stops}


def split_segments(lines):
    markers = [i for i, ln in enumerate(lines) if ln.strip().lower() in ("andata", "ritorno")]
    if not markers:
        return [build_segment(lines, "andata")]
    segs = []
    for k, idx in enumerate(markers):
        end = markers[k + 1] if k + 1 < len(markers) else len(lines)
        segs.append(build_segment(lines[idx + 1:end], lines[idx].strip().lower()))
    return segs


def parse_card(node):
    tm = TICKET_RE.search(str(node))
    ticket_id = tm.group(1) if tm else None
    lines = [ln.strip() for ln in node.get_text("\n").split("\n") if ln.strip()]
    full = "\n".join(lines)
    pm = PRICE_RE.search(full)
    price = parse_price(pm.group(1)) if pm else None
    sm = SUMMARY_TIMES_RE.search(full)
    summary = f"{sm.group(1)} → {sm.group(2)}" if sm else None
    seller_m = SELLER_RE.search(full)
    seller = seller_m.group(1).strip() if seller_m else None
    return {
        "id": ticket_id, "price": price, "summary": summary, "seller": seller,
        "segments": split_segments(lines),
        "link": f"{BASE}/payments/buyTrainTicket/{ticket_id}" if ticket_id else None,
    }


def parse_page(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    seen_ids, cards = set(), []
    for link in soup.select('a[href*="buyTrainTicket/"]'):
        card = parse_card(card_root_for(link))
        if card["id"] and card["id"] not in seen_ids:
            seen_ids.add(card["id"])
            cards.append(card)
    return cards


def segment_route_match(segment, dep_sub, arr_sub):
    stops = segment["stops"]
    dep_sub, arr_sub = (dep_sub or "").lower(), (arr_sub or "").lower()
    for i, (t_i, st_i) in enumerate(stops):
        if dep_sub and dep_sub not in st_i.lower():
            continue
        for j in range(i + 1, len(stops)):
            t_j, st_j = stops[j]
            if not arr_sub or arr_sub in st_j.lower():
                return (t_i, t_j)
    return None


def ticket_matches(card, search):
    dep_sub = search.get("match_departure_contains", "")
    arr_sub = search.get("match_arrival_contains", "")
    want_date = to_ddmmyyyy(search.get("match_date")) if search.get("match_date") else None
    time_from = norm_time(search["time_from"]) if search.get("time_from") else None
    time_to = norm_time(search["time_to"]) if search.get("time_to") else None
    max_price = search.get("max_price")
    if max_price is not None and card["price"] is not None and card["price"] > max_price:
        return None
    for seg in card["segments"]:
        if not dep_sub and not arr_sub:
            r = (seg["stops"][0][0], seg["stops"][-1][0]) if seg["stops"] else None
        else:
            r = segment_route_match(seg, dep_sub, arr_sub)
        if not r:
            continue
        dep_time, arr_time = r
        if want_date and seg.get("date") and seg["date"] != want_date:
            continue
        if time_from and dep_time < time_from:
            continue
        if time_to and dep_time > time_to:
            continue
        return {"kind": seg["kind"], "date": seg.get("date"),
                "dep_time": dep_time, "arr_time": arr_time}
    return None


def make_search(dep, arr, date_iso, tfrom, tto, maxp):
    dep_station, arr_station = to_station(dep), to_station(arr)
    url = BASE + SEARCH_PATH + "?" + urllib.parse.urlencode({
        "departure": dep_station, "departure_id": dep_station,
        "arrival": arr_station, "arrival_id": arr_station, "date": date_iso or ""})
    return {
        "name": f"{dep.title()} → {arr.title()}",
        "search_url": url,
        "match_departure_contains": city_key(dep),
        "match_arrival_contains": city_key(arr),
        "match_date": date_iso or "",
        "time_from": tfrom or "",
        "time_to": tto or "",
        "max_price": maxp,
    }


def search_summary(s):
    parts = [s.get("name", "?")]
    extra = []
    if s.get("match_date"):
        extra.append(f"📅 {to_ddmmyyyy(s['match_date'])}")
    if s.get("time_from") or s.get("time_to"):
        extra.append(f"🕒 {s.get('time_from') or '--'}–{s.get('time_to') or '--'}")
    if s.get("max_price") is not None:
        extra.append(f"💶 max {s['max_price']}€")
    if extra:
        parts.append(" · ".join(extra))
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Persistenza
# ---------------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


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
    raw = _load(SEARCHES_PATH, None)
    if raw is None or not isinstance(raw, dict):
        if os.path.exists(SEARCHES_PATH):
            log.error("searches.json illeggibile o di forma inattesa: non lo sovrascrivo. "
                      "Ripristinalo o rinominalo per ripartire da zero.")
            raise SystemExit(1)
        raw = {}
    now = dt.datetime.utcnow().timestamp()
    store = migrate_store(raw, OWNER, now)
    ensure_admin(store, OWNER, now=now)
    # L'admin è solo chi corrisponde a TELEGRAM_CHAT_ID: eventuali admin
    # residui di configurazioni precedenti vengono retrocessi a invitati.
    for uid, u in store["users"].items():
        if uid != str(OWNER) and u.get("role") == "admin":
            u["role"] = "guest"
    if store != raw:
        save_store(store)
    return store


def save_store(store):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SEARCHES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SEARCHES_PATH)


def seen_key(user_id, ticket_id):
    return f"{user_id}:{ticket_id}"


def migrate_seen(old, owner_id):
    """Le chiavi vecchie (solo id biglietto) vengono attribuite all'admin."""
    out = {}
    for k, v in old.items():
        out[k if ":" in k else seen_key(owner_id, k)] = v
    return out


def load_seen():
    raw = _load(SEEN_PATH, None)
    if raw is None:
        if os.path.exists(SEEN_PATH):
            log.warning("seen.json illeggibile: lo rinomino e riparto da zero.")
            try:
                os.replace(SEEN_PATH, SEEN_PATH + ".corrupt")
            except OSError as e:
                log.warning("impossibile mettere da parte seen.json corrotto: %s", e)
        raw = {}
    seen = migrate_seen(raw, OWNER)
    if seen != raw:
        seen = save_seen(seen)
    return seen


def save_seen(seen):
    os.makedirs(DATA_DIR, exist_ok=True)
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=SEEN_RETENTION_DAYS)).timestamp()
    for k in [k for k, v in seen.items() if v < cutoff]:
        del seen[k]
    tmp = SEEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SEEN_PATH)
    return seen


# ---------------------------------------------------------------------------
# Inviti (codici monouso generati dall'admin)
# ---------------------------------------------------------------------------
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # senza 0/O, 1/I
INVITE_TTL_SECONDS = 24 * 3600
INVITE_CODE_RE = re.compile(rf"^[{INVITE_ALPHABET}]{{6}}$")


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


# ---------------------------------------------------------------------------
# Helpers Telegram
# ---------------------------------------------------------------------------
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
        existing = store["users"].get(uid)
        if existing is None or existing.get("role") != "admin":
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


def esc(s):
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notify_text(search, card, match):
    lines = [f"🎟️ <b>Nuovo biglietto: {esc(search.get('name'))}</b>"]
    info = []
    if match.get("date"):
        info.append(f"📅 {esc(match['date'])}")
    if match.get("dep_time"):
        info.append(f"🕒 {esc(match['dep_time'])} → {esc(match.get('arr_time',''))}")
    if info:
        lines.append(" · ".join(info))
    if card.get("price") is not None:
        p = card["price"]
        lines.append(f"💶 {int(p) if float(p).is_integer() else p} €")
    if card.get("seller"):
        lines.append(f"👤 {esc(card['seller'])}")
    if card.get("link"):
        lines.append(f"\n🔗 <a href=\"{esc(card['link'])}\">Apri il biglietto</a>")
    return "\n".join(lines)


BTN_FIND = "🔎 Cerca ora"
BTN_NEW = "➕ Nuova ricerca"
BTN_LIST = "📋 Le mie ricerche"
BTN_PAUSE = "⏸️ Pausa"
BTN_RESUME = "▶️ Riprendi"
BTN_INVITE = "🔗 Invita"
BTN_USERS = "👥 Utenti"
MENU_LABELS = [BTN_FIND, BTN_NEW, BTN_LIST, BTN_PAUSE, BTN_RESUME, BTN_INVITE, BTN_USERS]
MENU_FILTER = filters.Text(MENU_LABELS)
WIZ_TEXT = filters.TEXT & ~filters.COMMAND & ~MENU_FILTER


def main_kb_rows(user):
    rows = [[BTN_FIND, BTN_NEW],
            [BTN_LIST, BTN_RESUME if user.get("paused") else BTN_PAUSE]]
    if is_admin(user):
        rows.append([BTN_INVITE, BTN_USERS])
    return rows


def main_kb(user):
    """Tastiera fissa sotto la casella di testo (sostituisce il vecchio menù inline)."""
    return ReplyKeyboardMarkup(main_kb_rows(user), resize_keyboard=True, is_persistent=True)


WELCOME = (
    "🎟️ <b>Bot Trovaunposto</b>\n"
    "Ti avviso qui appena compare un biglietto treno che cerchi.\n\n"
    "Usa i pulsanti qui sotto: <b>Nuova ricerca</b> ti guida passo passo, "
    "senza dover scrivere date o orari a mano."
)

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
    except Exception as e:  # noqa: BLE001
        log.warning("registrazione comandi (tutti) fallita: %s", e)
    try:
        await bot.set_my_commands(
            [BotCommand(c, d) for c, d in commands_for("admin")],
            scope=BotCommandScopeChat(chat_id=int(OWNER)))
    except Exception as e:  # noqa: BLE001
        log.warning("registrazione comandi (admin) fallita: %s", e)


# Stati del wizard
ASK_DEP, ASK_ARR, ASK_DAY, ASK_TIME, ASK_PRICE, ASK_CONFIRM = range(6)


# ---------------------------------------------------------------------------
# Comandi base
# ---------------------------------------------------------------------------
async def _handle_invite_code(update, context, code):
    """Prova a riscattare un codice per un utente NON autorizzato. Risponde sempre."""
    if update.effective_user is None:
        return False
    store = context.application.bot_data["store"]
    u = update.effective_user
    name = u.full_name or u.username or str(u.id)
    now = dt.datetime.utcnow().timestamp()
    if not redeem_invite(store, code, u.id, name, now):
        await update.effective_message.reply_text(INVALID_CODE_MSG, reply_markup=ReplyKeyboardRemove())
        return False
    save_store(store)
    new_user_rec = store["users"][str(u.id)]
    await update.effective_message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, reply_markup=main_kb(new_user_rec))
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
            await update.effective_message.reply_text(PRIVATE_MSG, reply_markup=ReplyKeyboardRemove())
        return
    await update.effective_message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, reply_markup=main_kb(user)
    )


async def on_unauthorized_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gruppo 1: agisce solo per chi NON è autorizzato (codice d'invito o messaggio 'privato')."""
    store = context.application.bot_data["store"]
    if get_user(update, store) is not None:
        return
    text = (update.effective_message.text or "").strip().upper()
    if INVITE_CODE_RE.match(text):
        await _handle_invite_code(update, context, text)
    else:
        await update.effective_message.reply_text(PRIVATE_MSG, reply_markup=ReplyKeyboardRemove())


async def on_stale_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bottone di un passo del wizard premuto quando nessun wizard è attivo (es. dopo un
    riavvio): rispondi invece di lasciarlo girare. Se un wizard è attivo ci pensa lui."""
    if "draft" in context.user_data:
        return
    try:
        await update.callback_query.answer(
            "Questo bottone non è più attivo: ricomincia dalla tastiera qui sotto.", show_alert=True)
    except Exception:  # noqa: BLE001
        pass


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


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE, user, edit=False):
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
    user = get_user(update, context.application.bot_data["store"])
    if user is None:
        return
    await show_list(update, context, user)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return
    user["paused"] = True
    save_store(store)
    await update.effective_message.reply_text(
        "⏸️ Notifiche sospese. Usa ▶️ Riprendi per riattivarle.", reply_markup=main_kb(user))


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return
    user["paused"] = False
    save_store(store)
    await update.effective_message.reply_text("▶️ Notifiche riattivate.", reply_markup=main_kb(user))


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


async def _delete_later(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    try:
        await context.bot.delete_message(chat_id=d["chat_id"], message_id=d["message_id"])
    except Exception:
        pass


def clear_batches(last_id, floor, size=100):
    """Blocchi di id messaggio da cancellare, dal più recente (last_id) fino a floor escluso."""
    floor = max(int(floor or 0), 0)
    cur = int(last_id)
    while cur > floor:
        lo = max(cur - size, floor)
        yield list(range(cur, lo, -1))
        cur = lo


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return
    chat_id = update.effective_chat.id
    last_id = update.effective_message.message_id
    # Telegram consente ai bot di cancellare solo i messaggi degli ultimi 2 giorni.
    # Cancelliamo a blocchi di 100 tutto ciò che è cancellabile, partendo dal
    # messaggio corrente e scendendo fino al punto dove si è fermata l'ultima
    # pulizia: ciò che stava sotto era già stato tolto (o era già troppo vecchio).
    floor = user.get("clear_floor", 0)
    for ids in clear_batches(last_id, floor):
        try:
            await context.bot.delete_messages(chat_id=chat_id, message_ids=ids)
        except Exception as e:  # noqa: BLE001
            # Il blocco contiene messaggi non cancellabili (troppo vecchi): provo uno a uno.
            # Se nessuno si cancella, tutto ciò che sta sotto è ancora più vecchio: fine.
            log.debug("delete_messages %s..%s: %s", ids[0], ids[-1], e)
            ok = 0
            for mid in ids:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                    ok += 1
                except Exception:  # noqa: BLE001
                    pass
            if ok == 0:
                break
        await asyncio.sleep(0.2)
    user["clear_floor"] = last_id
    save_store(store)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🧹 Fatto: ho cancellato i messaggi degli ultimi 2 giorni.\n"
             "Telegram non permette ai bot di rimuovere quelli più vecchi: per quelli usa "
             "“Cancella chat” dal menù di Telegram.")
    context.job_queue.run_once(
        _delete_later, 10, data={"chat_id": chat_id, "message_id": msg.message_id}
    )


async def cmd_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnostica temporanea: prova Milano→Roma dal server e riporta cosa riceve."""
    if not is_admin(get_user(update, context.application.bot_data["store"])):
        return
    url = ("https://trovaunposto.it/trains/searchTrainTicket?"
           "departure=MILANO%28TUTTE+LE+STAZIONI%29&departure_id=MILANO%28TUTTE+LE+STAZIONI%29&"
           "arrival=ROMA%28TUTTE+LE+STAZIONI%29&arrival_id=ROMA%28TUTTE+LE+STAZIONI%29&date=")
    await update.effective_message.reply_text("🔧 Test Milano→Roma dal server in corso…")
    try:
        html = await asyncio.to_thread(lambda: fetch(url))
    except Exception as e:  # noqa: BLE001
        await update.effective_message.reply_text(f"🔧 fetch ERRORE: {type(e).__name__}: {e}")
        return
    raw = html.count("buyTrainTicket")
    cards = parse_page(html)
    s = {"match_departure_contains": "MILANO", "match_arrival_contains": "ROMA"}
    matches = sum(1 for c in cards if ticket_matches(c, s))
    low = html.lower()
    flags = [k for k in ("just a moment", "cloudflare", "captcha", "enable javascript",
                         "access denied", "forbidden", "attiva javascript") if k in low]
    await update.effective_message.reply_text(
        "🔧 <b>DEBUG Milano→Roma</b>\n"
        f"HTML ricevuto: {len(html)} caratteri\n"
        f"link 'buyTrainTicket' grezzi: {raw}\n"
        f"card lette: {len(cards)}\n"
        f"corrispondenze MILANO→ROMA: {matches}\n"
        f"segnali di blocco: {', '.join(flags) if flags else 'nessuno'}",
        parse_mode=ParseMode.HTML,
    )


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
    save_store(store)
    text, kb = users_view(store)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ---------------------------------------------------------------------------
# Callback dei pulsanti del menu (fuori dal wizard)
# ---------------------------------------------------------------------------
async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return
    data = q.data
    if data == "list":
        await show_list(update, context, user, edit=True)
    elif data == "pause":
        user["paused"] = True
        save_store(store)
        try:
            await q.edit_message_reply_markup(None)
        except Exception:  # noqa: BLE001
            pass
        await update.effective_chat.send_message("⏸️ Notifiche sospese.", reply_markup=main_kb(user))
    elif data == "resume":
        user["paused"] = False
        save_store(store)
        try:
            await q.edit_message_reply_markup(None)
        except Exception:  # noqa: BLE001
            pass
        await update.effective_chat.send_message("▶️ Notifiche riattivate.", reply_markup=main_kb(user))
    elif data.startswith("del:"):
        idx = int(data.split(":")[1])
        if 0 <= idx < len(user["searches"]):
            removed = user["searches"].pop(idx)
            save_store(store)
            await q.edit_message_text(f"🗑 Rimossa: {esc(search_summary(removed))}")
        await show_list(update, context, user)
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
            await context.bot.send_message(
                chat_id=uid, text="Il tuo accesso al bot è stato revocato.",
                reply_markup=ReplyKeyboardRemove())
        except Exception as e:  # noqa: BLE001
            log.warning("avviso revoca fallito: %s", e)
        text, kb = users_view(store)
        await q.edit_message_text(f"🗑 Accesso revocato a <b>{esc(name)}</b>.\n\n{text}",
                                  parse_mode=ParseMode.HTML, reply_markup=kb)


# ---------------------------------------------------------------------------
# WIZARD "Nuova ricerca"
# ---------------------------------------------------------------------------
def cities_kb():
    rows, row = [], []
    for c in POPULAR_CITIES:
        row.append(InlineKeyboardButton(c, callback_data=f"city|{c}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✍️ Altra città", callback_data="city|__other__")])
    return InlineKeyboardMarkup(rows)


def days_kb():
    today = dt.datetime.now(TZ).date()
    giorni = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
    rows, row = [], []
    for i in range(7):
        d = today + dt.timedelta(days=i)
        if i == 0:
            label = "Oggi"
        elif i == 1:
            label = "Domani"
        else:
            label = f"{giorni[d.weekday()]} {d.day:02d}/{d.month:02d}"
        row.append(InlineKeyboardButton(label, callback_data=f"day|{d.isoformat()}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("Qualsiasi giorno", callback_data="day|any"),
        InlineKeyboardButton("✍️ Altra data", callback_data="day|__other__"),
    ])
    return InlineKeyboardMarkup(rows)


def time_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Qualsiasi ora", callback_data="time|any")],
        [InlineKeyboardButton("Mattina 06–12", callback_data="time|06:00-12:00"),
         InlineKeyboardButton("Pomeriggio 12–18", callback_data="time|12:00-18:00")],
        [InlineKeyboardButton("Sera 18–24", callback_data="time|18:00-23:59"),
         InlineKeyboardButton("✍️ Personalizzata", callback_data="time|__other__")],
    ])


def price_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Nessun limite", callback_data="price|none")],
        [InlineKeyboardButton("≤ 30€", callback_data="price|30"),
         InlineKeyboardButton("≤ 50€", callback_data="price|50"),
         InlineKeyboardButton("≤ 80€", callback_data="price|80")],
        [InlineKeyboardButton("✍️ Altro importo", callback_data="price|__other__")],
    ])


def confirm_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Conferma", callback_data="confirm|ok")],
        [InlineKeyboardButton("🔁 Ricomincia", callback_data="confirm|restart"),
         InlineKeyboardButton("❌ Annulla", callback_data="confirm|cancel")],
    ])


def after_add_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Aggiungi anche il ritorno", callback_data="return")],
    ])


def after_search_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Salva come ricerca", callback_data="savelast")]])


def draft_search(d):
    return make_search(d.get("dep", ""), d.get("arr", ""), d.get("date", ""),
                       d.get("tfrom", ""), d.get("tto", ""), d.get("maxp"))


def reverse_route(draft):
    return {"dep": draft.get("arr", ""), "arr": draft.get("dep", "")}


def confirm_text(search):
    return f"📝 <b>Riepilogo</b>\n{esc(search_summary(search))}\n\nConfermi?"


async def _reject_if_over_limit(update, user):
    """True (dopo aver risposto) se l'utente ha già raggiunto il tetto di ricerche."""
    limit = search_limit(user)
    if len(user["searches"]) < limit:
        return False
    if update.callback_query:
        await update.effective_chat.send_message(limit_msg(limit), parse_mode=ParseMode.HTML)
    else:
        await update.effective_message.reply_text(limit_msg(limit), parse_mode=ParseMode.HTML)
    return True


async def wiz_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None or await _reject_if_over_limit(update, user):
        if update.callback_query:
            await update.callback_query.answer()
        return ConversationHandler.END
    context.user_data["mode"] = "add"
    return await wiz_start(update, context)


async def wiz_return(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bottone 'Aggiungi anche il ritorno': città invertite, si riparte dal giorno."""
    q = update.callback_query
    await q.answer()
    try:
        await q.edit_message_reply_markup(None)
    except Exception:  # noqa: BLE001
        pass
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return ConversationHandler.END
    route = context.user_data.get("last_route")
    if not route:
        await update.effective_chat.send_message("Ricomincia da ➕ Nuova ricerca.")
        return ConversationHandler.END
    if await _reject_if_over_limit(update, user):
        return ConversationHandler.END
    context.user_data["mode"] = "add"
    d = reverse_route(route)
    context.user_data["draft"] = d
    await update.effective_chat.send_message(
        f"🔁 <b>Ritorno: {esc(d['dep'].title())} → {esc(d['arr'].title())}</b>\nChe giorno?",
        parse_mode=ParseMode.HTML, reply_markup=days_kb())
    return ASK_DAY


async def wiz_save_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bottone 'Salva come ricerca' dopo Cerca ora: riusa i criteri appena cercati e va al riepilogo."""
    q = update.callback_query
    await q.answer()
    try:
        await q.edit_message_reply_markup(None)
    except Exception:  # noqa: BLE001
        pass
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        return ConversationHandler.END
    d = context.user_data.get("last_draft")
    if not d:
        await update.effective_chat.send_message("Ricomincia da ➕ Nuova ricerca.")
        return ConversationHandler.END
    if await _reject_if_over_limit(update, user):
        return ConversationHandler.END
    context.user_data["mode"] = "add"
    context.user_data["draft"] = dict(d)
    await update.effective_chat.send_message(confirm_text(draft_search(d)), parse_mode=ParseMode.HTML,
                               reply_markup=confirm_kb())
    return ASK_CONFIRM


async def wiz_start_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "search"
    return await wiz_start(update, context)


async def wiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user(update, context.application.bot_data["store"]) is None:
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:  # noqa: BLE001
                pass
        return ConversationHandler.END
    context.user_data["draft"] = {}
    text = "🚆 <b>Da dove parti?</b>\nScegli una città o scrivine un'altra."
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:  # noqa: BLE001
            pass
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=cities_kb())
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=cities_kb())
    return ASK_DEP


async def wiz_dep_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split("|", 1)[1]
    if val == "__other__":
        await q.message.reply_text("Scrivimi la città di <b>partenza</b>:", parse_mode=ParseMode.HTML)
        return ASK_DEP
    context.user_data["draft"]["dep"] = val
    await q.message.reply_text("🏁 <b>Dove arrivi?</b>", parse_mode=ParseMode.HTML, reply_markup=cities_kb())
    return ASK_ARR


async def wiz_dep_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"]["dep"] = update.message.text.strip()
    await update.message.reply_text("🏁 <b>Dove arrivi?</b>", parse_mode=ParseMode.HTML, reply_markup=cities_kb())
    return ASK_ARR


async def wiz_arr_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split("|", 1)[1]
    if val == "__other__":
        await q.message.reply_text("Scrivimi la città di <b>arrivo</b>:", parse_mode=ParseMode.HTML)
        return ASK_ARR
    context.user_data["draft"]["arr"] = val
    await q.message.reply_text("📅 <b>Per quale giorno?</b>", parse_mode=ParseMode.HTML, reply_markup=days_kb())
    return ASK_DAY


async def wiz_arr_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"]["arr"] = update.message.text.strip()
    await update.message.reply_text("📅 <b>Per quale giorno?</b>", parse_mode=ParseMode.HTML, reply_markup=days_kb())
    return ASK_DAY


async def wiz_day_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split("|", 1)[1]
    if val == "__other__":
        await q.message.reply_text("Scrivimi la data (gg/mm/aaaa), es. 25/07/2026:")
        return ASK_DAY
    context.user_data["draft"]["date"] = "" if val == "any" else val
    await q.message.reply_text("🕒 <b>In che fascia oraria?</b>", parse_mode=ParseMode.HTML, reply_markup=time_kb())
    return ASK_TIME


async def wiz_day_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    if not m:
        await update.message.reply_text("Formato non valido. Scrivila come gg/mm/aaaa, es. 25/07/2026.")
        return ASK_DAY
    context.user_data["draft"]["date"] = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    await update.message.reply_text("🕒 <b>In che fascia oraria?</b>", parse_mode=ParseMode.HTML, reply_markup=time_kb())
    return ASK_TIME


async def wiz_time_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split("|", 1)[1]
    if val == "__other__":
        await q.message.reply_text("Scrivimi la fascia come HH:MM-HH:MM, es. 17:00-21:00:")
        return ASK_TIME
    if val == "any":
        context.user_data["draft"]["tfrom"] = ""
        context.user_data["draft"]["tto"] = ""
    else:
        a, b = val.split("-")
        context.user_data["draft"]["tfrom"] = a
        context.user_data["draft"]["tto"] = b
    await q.message.reply_text("💶 <b>Prezzo massimo?</b>", parse_mode=ParseMode.HTML, reply_markup=price_kb())
    return ASK_PRICE


async def wiz_time_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    m = re.match(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$", raw)
    if not m:
        await update.message.reply_text("Formato non valido. Esempio: 17:00-21:00.")
        return ASK_TIME
    context.user_data["draft"]["tfrom"] = norm_time(m.group(1))
    context.user_data["draft"]["tto"] = norm_time(m.group(2))
    await update.message.reply_text("💶 <b>Prezzo massimo?</b>", parse_mode=ParseMode.HTML, reply_markup=price_kb())
    return ASK_PRICE


# Con 'qualsiasi giorno' mostriamo i biglietti da oggi fino a +N giorni.
ANY_DAY_HORIZON = 3


def search_urls_for(search):
    """URL da interrogare. Con una data specifica: quella. Con 'qualsiasi giorno'
    (match_date vuoto): una finestra centrata su oggi (copre i prossimi giorni),
    perché il parametro date vuoto non restituisce risultati affidabili dal server."""
    base_url = build_search_url(search)
    if search.get("match_date"):
        return [base_url]
    d = dt.datetime.now(TZ).date().isoformat()
    if re.search(r"[?&]date=", base_url):
        return [re.sub(r"(date=)[^&]*", r"\g<1>" + d, base_url)]
    sep = "&" if "?" in base_url else "?"
    return [f"{base_url}{sep}date={d}"]


def _within_days(date_str, today, days):
    """True se date_str (gg/mm/aaaa) è tra oggi e oggi+days (inclusi)."""
    if not date_str:
        return True
    try:
        d = dt.datetime.strptime(date_str, "%d/%m/%Y").date()
    except Exception:
        return True
    return today <= d <= today + dt.timedelta(days=days)


def find_matches(search):
    """Scarica i biglietti corrispondenti e li ritorna ordinati, senza duplicati.
    Per 'qualsiasi giorno' limita ai prossimi giorni (oggi + ANY_DAY_HORIZON)."""
    import time as _t
    cards_by_id = {}
    ok = False
    urls = search_urls_for(search)
    for i, url in enumerate(urls):
        try:
            for c in parse_page(fetch(url)):
                cards_by_id[c["id"]] = c
            ok = True
        except Exception:
            pass
        if i + 1 < len(urls):
            _t.sleep(0.4)
    if not ok:
        raise RuntimeError("sito irraggiungibile (fetch fallito)")
    any_day = not search.get("match_date")
    today = dt.datetime.now(TZ).date()
    out = []
    for c in cards_by_id.values():
        m = ticket_matches(c, search)
        if not m:
            continue
        if any_day and not _within_days(m.get("date"), today, ANY_DAY_HORIZON):
            continue
        out.append((c, m))
    out.sort(key=lambda cm: (cm[1].get("date") or "", cm[1].get("dep_time") or ""))
    return out


def render_matches(search, matches):
    """Messaggio con l'elenco dei biglietti disponibili ora."""
    if not matches:
        return (f"🔎 <b>{esc(search.get('name'))}</b>\n"
                "Al momento non c'è nessun biglietto che rispetta i criteri.")
    lines = [f"🔎 <b>{esc(search.get('name'))}</b> — {len(matches)} disponibili ora:"]
    for i, (card, m) in enumerate(matches[:15], 1):
        date = f"📅 {esc(m['date'])} " if m.get("date") else ""
        tempo = f"🕒 {esc(m.get('dep_time',''))}→{esc(m.get('arr_time',''))}"
        prezzo = ""
        if card.get("price") is not None:
            p = card["price"]
            prezzo = f" · 💶 {int(p) if float(p).is_integer() else p}€"
        riga = f"{i}. {date}{tempo}{prezzo}"
        if card.get("link"):
            riga += f" — <a href=\"{esc(card['link'])}\">apri</a>"
        lines.append(riga)
    if len(matches) > 15:
        lines.append(f"…e altri {len(matches) - 15}.")
    return "\n".join(lines)


async def _finish(update, context, send):
    store = context.application.bot_data["store"]
    user = get_user(update, store)
    if user is None:
        context.user_data.pop("draft", None)
        context.user_data.pop("mode", None)
        return ConversationHandler.END
    d = context.user_data.get("draft", {})
    mode = context.user_data.get("mode", "add")
    search = draft_search(d)

    if mode == "search":
        # Sola consultazione: NON salva la ricerca e non registra nulla.
        context.user_data["last_draft"] = dict(d)
        try:
            matches = await asyncio.to_thread(lambda: find_matches(search))
            await send(render_matches(search, matches))
        except Exception:
            await send("Non sono riuscito a leggere i biglietti ora; riprova tra poco.")
        await send("ℹ️ Solo consultazione: questa ricerca non è stata salvata.", reply_markup=after_search_kb())
        context.user_data.pop("draft", None)
        context.user_data.pop("mode", None)
        return ConversationHandler.END

    # mode == "add": salva la ricerca dell'utente e attiva gli avvisi.
    uid = str(update.effective_user.id)
    limit = search_limit(user)
    if len(user["searches"]) >= limit:
        await send(limit_msg(limit))
        context.user_data.pop("draft", None)
        context.user_data.pop("mode", None)
        return ConversationHandler.END
    user["searches"].append(search)
    save_store(store)
    context.user_data["last_route"] = {"dep": d.get("dep", ""), "arr": d.get("arr", "")}
    await send(f"✅ <b>Ricerca creata!</b>\n{esc(search_summary(search))}")
    # mostra i biglietti già disponibili ora e li registra (senza riavvisare)
    seen = context.application.bot_data["seen"]
    try:
        matches = await asyncio.to_thread(lambda: find_matches(search))
        now = dt.datetime.utcnow().timestamp()
        for card, _m in matches:
            seen[seen_key(uid, card["id"])] = now
        context.application.bot_data["seen"] = save_seen(seen)
        await send(render_matches(search, matches))
    except Exception:
        await send("Non sono riuscito a leggere i biglietti ora; li controllerò al prossimo giro.")
    await send("Da ora ti avviserò dei <b>nuovi</b> biglietti che compaiono.", reply_markup=after_add_kb())
    context.user_data.pop("draft", None)
    context.user_data.pop("mode", None)
    return ConversationHandler.END


async def wiz_price_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split("|", 1)[1]
    if val == "__other__":
        await q.message.reply_text("Scrivimi il prezzo massimo in euro, es. 45:")
        return ASK_PRICE
    context.user_data["draft"]["maxp"] = None if val == "none" else int(val)
    return await _after_price(update, context,
                              lambda t, **kw: q.message.reply_text(
                                  t, parse_mode=ParseMode.HTML, disable_web_page_preview=True, **kw))


async def wiz_price_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace("€", "").strip()
    if not raw.isdigit():
        await update.message.reply_text("Scrivi solo un numero, es. 45 (oppure premi 'Nessun limite').")
        return ASK_PRICE
    context.user_data["draft"]["maxp"] = int(raw)
    return await _after_price(update, context,
                              lambda t, **kw: update.message.reply_text(
                                  t, parse_mode=ParseMode.HTML, disable_web_page_preview=True, **kw))


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
                             lambda t, **kw: update.effective_chat.send_message(
                                 t, parse_mode=ParseMode.HTML, disable_web_page_preview=True, **kw))
    if val == "restart":
        try:
            await q.edit_message_reply_markup(None)
        except Exception:  # noqa: BLE001
            pass
        return await wiz_start(update, context)
    context.user_data.pop("draft", None)
    context.user_data.pop("mode", None)
    await q.edit_message_text("Operazione annullata.")
    return ConversationHandler.END


async def wiz_confirm_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Usa i bottoni qui sopra: ✅ Conferma, 🔁 Ricomincia o ❌ Annulla.")
    return ASK_CONFIRM


async def wiz_menu_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Un bottone del menù premuto durante il wizard: annulla il wizard ed esegue l'azione."""
    context.user_data.pop("draft", None)
    context.user_data.pop("mode", None)
    await on_menu_text(update, context)
    return ConversationHandler.END


async def wiz_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("draft", None)
    context.user_data.pop("mode", None)
    await update.effective_message.reply_text("Operazione annullata.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Controllo periodico dei biglietti
# ---------------------------------------------------------------------------
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


async def check_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    store = app.bot_data["store"]
    todo = list(iter_searches(store, only_active=True))
    if not todo:
        return
    now = dt.datetime.utcnow().timestamp()
    started = now
    # Auto-rallentamento: se il sito è in difficoltà, salta i controlli finché non scade.
    if now < app.bot_data.get("backoff_until", 0):
        return
    seen = app.bot_data["seen"]
    changed = False
    attempted = failures = 0
    for uid, search in todo:
        if uid not in store["users"]:
            continue
        attempted += 1
        try:
            matches = await asyncio.to_thread(lambda s=search: find_matches(s))
        except Exception as e:  # noqa: BLE001
            failures += 1
            log.warning("controllo fallito per %s (%s): %s", search.get("name"), uid, e)
            continue
        # utente revocato nel frattempo: niente avvisi né memoria
        if uid not in store["users"]:
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

    elapsed = dt.datetime.utcnow().timestamp() - started
    if elapsed > CHECK_INTERVAL:
        log.warning("giro di controllo lungo: %.0fs su %d ricerche (intervallo %ds)",
                    elapsed, attempted, CHECK_INTERVAL)


async def on_startup(app: Application):
    app.bot_data["store"] = load_store()
    app.bot_data["seen"] = load_seen()
    store = app.bot_data["store"]
    await register_commands(app.bot)
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
                                   reply_markup=main_kb(admin))
    except Exception as e:  # noqa: BLE001
        log.warning("Impossibile inviare il messaggio di avvio: %s", e)


def build_application():
    if not TOKEN or not OWNER:
        raise SystemExit("Imposta le variabili TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.")
    app = Application.builder().token(TOKEN).post_init(on_startup).build()

    wizard = ConversationHandler(
        entry_points=[
            CommandHandler("aggiungi", wiz_start_add),
            CommandHandler("cerca", wiz_start_search),
            CallbackQueryHandler(wiz_start_add, pattern=r"^new$"),
            CallbackQueryHandler(wiz_start_search, pattern=r"^find$"),
            CallbackQueryHandler(wiz_return, pattern=r"^return$"),
            CallbackQueryHandler(wiz_save_last, pattern=r"^savelast$"),
            MessageHandler(filters.Text([BTN_NEW]), wiz_start_add),
            MessageHandler(filters.Text([BTN_FIND]), wiz_start_search),
        ],
        states={
            ASK_DEP: [CallbackQueryHandler(wiz_dep_btn, pattern=r"^city\|"),
                      MessageHandler(WIZ_TEXT, wiz_dep_txt)],
            ASK_ARR: [CallbackQueryHandler(wiz_arr_btn, pattern=r"^city\|"),
                      MessageHandler(WIZ_TEXT, wiz_arr_txt)],
            ASK_DAY: [CallbackQueryHandler(wiz_day_btn, pattern=r"^day\|"),
                      MessageHandler(WIZ_TEXT, wiz_day_txt)],
            ASK_TIME: [CallbackQueryHandler(wiz_time_btn, pattern=r"^time\|"),
                       MessageHandler(WIZ_TEXT, wiz_time_txt)],
            ASK_PRICE: [CallbackQueryHandler(wiz_price_btn, pattern=r"^price\|"),
                        MessageHandler(WIZ_TEXT, wiz_price_txt)],
            ASK_CONFIRM: [CallbackQueryHandler(wiz_confirm_btn, pattern=r"^confirm\|"),
                          MessageHandler(WIZ_TEXT, wiz_confirm_txt)],
        },
        fallbacks=[CommandHandler("annulla", wiz_cancel), MessageHandler(MENU_FILTER, wiz_menu_fallback)],
        allow_reentry=True,
    )

    app.add_handler(wizard)
    app.add_handler(MessageHandler(
        filters.Text([BTN_LIST, BTN_PAUSE, BTN_RESUME, BTN_INVITE, BTN_USERS]), on_menu_text))
    app.add_handler(CommandHandler(["start", "aiuto", "help"], cmd_start))
    app.add_handler(CommandHandler(["lista", "ricerche"], cmd_list))
    app.add_handler(CommandHandler("pausa", cmd_pause))
    app.add_handler(CommandHandler(["riprendi", "riattiva"], cmd_resume))
    app.add_handler(CommandHandler("stato", cmd_status))
    app.add_handler(CommandHandler(["pulisci", "clear"], cmd_clear))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CommandHandler("invita", cmd_invite))
    app.add_handler(CommandHandler("utenti", cmd_users))
    app.add_handler(CallbackQueryHandler(
        on_menu,
        pattern=r"^(list|pause|resume|del:\d+|avail:\d+|revoke:\d+|revoke_ok:\d+|revoke_no)$"))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.Regex(r"^/start\b") & filters.ChatType.PRIVATE,
        on_unauthorized_text), group=1)
    app.add_handler(CallbackQueryHandler(
        on_stale_callback, pattern=r"^(city|day|time|price|confirm)\|"), group=2)

    app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    return app


def main():
    app = build_application()
    log.info("Bot in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
