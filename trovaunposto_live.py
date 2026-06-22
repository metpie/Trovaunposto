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
  TELEGRAM_BOT_TOKEN  (obbligatoria)
  TELEGRAM_CHAT_ID    (obbligatoria: il tuo id numerico; riceve le notifiche ed è
                       l'unico autorizzato a comandare il bot)
  CHECK_INTERVAL      (opzionale, secondi tra un controllo e l'altro, default 60)
  DATA_DIR            (opzionale, cartella per i dati persistenti, default ./data)
"""

import asyncio
import datetime as dt
import json
import logging
import os
import re
import urllib.parse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER = os.environ.get("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))
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


def load_store():
    os.makedirs(DATA_DIR, exist_ok=True)
    store = _load(SEARCHES_PATH, {"searches": [], "paused": False})
    store.setdefault("searches", [])
    store.setdefault("paused", False)
    return store


def save_store(store):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SEARCHES_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def load_seen():
    return _load(SEEN_PATH, {})


def save_seen(seen):
    os.makedirs(DATA_DIR, exist_ok=True)
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=SEEN_RETENTION_DAYS)).timestamp()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)
    return seen


# ---------------------------------------------------------------------------
# Helpers Telegram
# ---------------------------------------------------------------------------
def is_owner(update: Update) -> bool:
    u = update.effective_user
    return u is not None and str(u.id) == str(OWNER)


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


def main_menu_kb(paused):
    rows = [
        [InlineKeyboardButton("➕ Nuova ricerca", callback_data="new")],
        [InlineKeyboardButton("📋 Le mie ricerche", callback_data="list")],
        [InlineKeyboardButton("▶️ Riprendi" if paused else "⏸️ Pausa",
                               callback_data="resume" if paused else "pause")],
    ]
    return InlineKeyboardMarkup(rows)


WELCOME = (
    "🎟️ <b>Bot Trovaunposto</b>\n"
    "Ti avviso qui appena compare un biglietto treno che cerchi.\n\n"
    "Usa i pulsanti qui sotto: <b>Nuova ricerca</b> ti guida passo passo, "
    "senza dover scrivere date o orari a mano."
)

# Stati del wizard
ASK_DEP, ASK_ARR, ASK_DAY, ASK_TIME, ASK_PRICE = range(5)


# ---------------------------------------------------------------------------
# Comandi base
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    store = context.application.bot_data["store"]
    await update.effective_message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(store["paused"])
    )


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    store = context.application.bot_data["store"]
    searches = store["searches"]
    if not searches:
        text = "Non hai ancora ricerche attive.\nPremi ➕ <b>Nuova ricerca</b> per crearne una."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Nuova ricerca", callback_data="new")]])
    else:
        text = "📋 <b>Le tue ricerche attive:</b>\n\n" + "\n".join(
            f"{i+1}. {esc(search_summary(s))}" for i, s in enumerate(searches)
        )
        rows = [[InlineKeyboardButton(f"🗑 Rimuovi #{i+1}", callback_data=f"del:{i}")]
                for i in range(len(searches))]
        rows.append([InlineKeyboardButton("➕ Nuova ricerca", callback_data="new")])
        kb = InlineKeyboardMarkup(rows)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await show_list(update, context)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    store = context.application.bot_data["store"]
    store["paused"] = True
    save_store(store)
    await update.effective_message.reply_text("⏸️ Notifiche sospese. Usa /riprendi per riattivarle.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    store = context.application.bot_data["store"]
    store["paused"] = False
    save_store(store)
    await update.effective_message.reply_text("▶️ Notifiche riattivate.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    store = context.application.bot_data["store"]
    stato = "in pausa ⏸️" if store["paused"] else "attivo ✅"
    await update.effective_message.reply_text(
        f"Stato: <b>{stato}</b>\nRicerche attive: {len(store['searches'])}\n"
        f"Controllo ogni {CHECK_INTERVAL}s.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Callback dei pulsanti del menu (fuori dal wizard)
# ---------------------------------------------------------------------------
async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    q = update.callback_query
    await q.answer()
    data = q.data
    store = context.application.bot_data["store"]
    if data == "list":
        await show_list(update, context, edit=True)
    elif data == "pause":
        store["paused"] = True
        save_store(store)
        await q.edit_message_text("⏸️ Notifiche sospese.", reply_markup=main_menu_kb(True))
    elif data == "resume":
        store["paused"] = False
        save_store(store)
        await q.edit_message_text("▶️ Notifiche riattivate.", reply_markup=main_menu_kb(False))
    elif data.startswith("del:"):
        idx = int(data.split(":")[1])
        if 0 <= idx < len(store["searches"]):
            removed = store["searches"].pop(idx)
            save_store(store)
            await q.edit_message_text(f"🗑 Rimossa: {esc(search_summary(removed))}")
        await show_list(update, context)


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


async def wiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    context.user_data["draft"] = {}
    text = "🚆 <b>Da dove parti?</b>\nScegli una città o scrivine un'altra."
    if update.callback_query:
        await update.callback_query.answer()
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


async def _finish(update, context, send):
    d = context.user_data.get("draft", {})
    search = make_search(d.get("dep", ""), d.get("arr", ""), d.get("date", ""),
                         d.get("tfrom", ""), d.get("tto", ""), d.get("maxp"))
    store = context.application.bot_data["store"]
    store["searches"].append(search)
    save_store(store)
    # registra i biglietti già presenti senza avvisare (niente valanga iniziale)
    seen = context.application.bot_data["seen"]
    try:
        cards = await asyncio.to_thread(lambda: parse_page(fetch(build_search_url(search))))
        now = dt.datetime.utcnow().timestamp()
        n = 0
        for c in cards:
            if ticket_matches(c, search):
                seen[c["id"]] = now
                n += 1
        context.application.bot_data["seen"] = save_seen(seen)
        extra = f"\nAl momento ci sono {n} biglietti che corrispondono; ti avviserò dei <b>prossimi</b>."
    except Exception:
        extra = "\nControllerò i biglietti al prossimo giro."
    await send(f"✅ <b>Ricerca creata!</b>\n{esc(search_summary(search))}{extra}")
    context.user_data.pop("draft", None)
    return ConversationHandler.END


async def wiz_price_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split("|", 1)[1]
    if val == "__other__":
        await q.message.reply_text("Scrivimi il prezzo massimo in euro, es. 45:")
        return ASK_PRICE
    context.user_data["draft"]["maxp"] = None if val == "none" else int(val)
    return await _finish(update, context, lambda t: q.message.reply_text(t, parse_mode=ParseMode.HTML))


async def wiz_price_txt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace("€", "").strip()
    if not raw.isdigit():
        await update.message.reply_text("Scrivi solo un numero, es. 45 (oppure premi 'Nessun limite').")
        return ASK_PRICE
    context.user_data["draft"]["maxp"] = int(raw)
    return await _finish(update, context, lambda t: update.message.reply_text(t, parse_mode=ParseMode.HTML))


async def wiz_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("draft", None)
    await update.effective_message.reply_text("Operazione annullata.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Controllo periodico dei biglietti
# ---------------------------------------------------------------------------
async def check_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    store = app.bot_data["store"]
    if store.get("paused") or not store.get("searches"):
        return
    seen = app.bot_data["seen"]
    now = dt.datetime.utcnow().timestamp()
    changed = False
    for search in list(store["searches"]):
        try:
            cards = await asyncio.to_thread(lambda s=search: parse_page(fetch(build_search_url(s))))
        except Exception as e:  # noqa: BLE001
            log.warning("controllo fallito per %s: %s", search.get("name"), e)
            continue
        for card in cards:
            m = ticket_matches(card, search)
            if not m or card["id"] in seen:
                continue
            seen[card["id"]] = now
            changed = True
            try:
                await context.bot.send_message(
                    chat_id=OWNER, text=notify_text(search, card, m),
                    parse_mode=ParseMode.HTML, disable_web_page_preview=False)
            except Exception as e:  # noqa: BLE001
                log.warning("invio notifica fallito: %s", e)
    if changed:
        app.bot_data["seen"] = save_seen(seen)


async def on_startup(app: Application):
    app.bot_data["store"] = load_store()
    app.bot_data["seen"] = load_seen()
    # primo avvio "pulito": se non ho memoria, registro i biglietti attuali in
    # silenzio così non parte una valanga di notifiche al primo giro.
    if not app.bot_data["seen"] and app.bot_data["store"]["searches"]:
        seen = {}
        now = dt.datetime.utcnow().timestamp()
        for search in app.bot_data["store"]["searches"]:
            try:
                cards = await asyncio.to_thread(lambda s=search: parse_page(fetch(build_search_url(s))))
            except Exception:
                continue
            for c in cards:
                if ticket_matches(c, search):
                    seen[c["id"]] = now
        app.bot_data["seen"] = save_seen(seen)
    log.info("Avviato. Ricerche: %d, intervallo %ds.",
             len(app.bot_data["store"]["searches"]), CHECK_INTERVAL)
    try:
        await app.bot.send_message(chat_id=OWNER, text=WELCOME, parse_mode=ParseMode.HTML,
                                   reply_markup=main_menu_kb(app.bot_data["store"]["paused"]))
    except Exception as e:  # noqa: BLE001
        log.warning("Impossibile inviare il messaggio di avvio: %s", e)


def build_application():
    if not TOKEN or not OWNER:
        raise SystemExit("Imposta le variabili TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.")
    app = Application.builder().token(TOKEN).post_init(on_startup).build()

    wizard = ConversationHandler(
        entry_points=[
            CommandHandler("aggiungi", wiz_start),
            CallbackQueryHandler(wiz_start, pattern=r"^new$"),
        ],
        states={
            ASK_DEP: [CallbackQueryHandler(wiz_dep_btn, pattern=r"^city\|"),
                      MessageHandler(filters.TEXT & ~filters.COMMAND, wiz_dep_txt)],
            ASK_ARR: [CallbackQueryHandler(wiz_arr_btn, pattern=r"^city\|"),
                      MessageHandler(filters.TEXT & ~filters.COMMAND, wiz_arr_txt)],
            ASK_DAY: [CallbackQueryHandler(wiz_day_btn, pattern=r"^day\|"),
                      MessageHandler(filters.TEXT & ~filters.COMMAND, wiz_day_txt)],
            ASK_TIME: [CallbackQueryHandler(wiz_time_btn, pattern=r"^time\|"),
                       MessageHandler(filters.TEXT & ~filters.COMMAND, wiz_time_txt)],
            ASK_PRICE: [CallbackQueryHandler(wiz_price_btn, pattern=r"^price\|"),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, wiz_price_txt)],
        },
        fallbacks=[CommandHandler("annulla", wiz_cancel)],
        allow_reentry=True,
    )

    app.add_handler(wizard)
    app.add_handler(CommandHandler(["start", "aiuto", "help"], cmd_start))
    app.add_handler(CommandHandler(["lista", "ricerche"], cmd_list))
    app.add_handler(CommandHandler("pausa", cmd_pause))
    app.add_handler(CommandHandler(["riprendi", "riattiva"], cmd_resume))
    app.add_handler(CommandHandler("stato", cmd_status))
    app.add_handler(CallbackQueryHandler(on_menu, pattern=r"^(list|pause|resume|del:\d+)$"))

    app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=10)
    return app


def main():
    app = build_application()
    log.info("Bot in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
