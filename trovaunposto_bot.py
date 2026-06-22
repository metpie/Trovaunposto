#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trovaunposto bot — controlla trovaunposto.it e avvisa su Telegram quando
compaiono nuovi biglietti treno che rispettano i criteri scelti (tratta,
giorno, fascia oraria, prezzo massimo).

Uso:
    python trovaunposto_bot.py            # giro normale (legge comandi Telegram + cerca)
    python trovaunposto_bot.py --dry-run  # stampa i risultati senza inviare nulla
    python trovaunposto_bot.py --test     # invia un messaggio di prova su Telegram
    python trovaunposto_bot.py --all      # notifica anche i biglietti gia' presenti (ignora lo storico)

Le ricerche si impostano da Telegram con /aggiungi /lista /rimuovi /pausa
(vedi /aiuto), oppure a mano in config.json.

Configurazione: vedi config.json (tratte e filtri) e le variabili d'ambiente
TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Percorsi e costanti
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state", "seen.json")

BASE = "https://trovaunposto.it"
SEARCH_PATH = "/trains/searchTrainTicket"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Regex
TICKET_RE = re.compile(r"/payments/buyTrainTicket/(\d+)")
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*€")
SUMMARY_TIMES_RE = re.compile(r"(\d{1,2}:\d{2})\s*>\s*(\d{1,2}:\d{2})")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")
SELLER_RE = re.compile(r"Biglietto di\s+([^\n]+)")

# Testo che segna la fine della parte "utile" di una tratta (andata/ritorno)
TERMINATORS = [
    "Cambio nominativo",
    "Informativa sui biglietti",
    "Seleziona biglietto",
    "Biglietto di",
    "Garanzia Trovaunposto",
    "Ogni transazione",
    "Scopri di più",
    "Scopri di piu",
]

MAX_NOTIFICATIONS_PER_RUN = 15  # evita inondazioni se appaiono tanti biglietti insieme
STATE_RETENTION_DAYS = 45       # dopo quanti giorni dimenticare un ID gia' visto


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def log(msg):
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def norm_time(t):
    """'5:38' -> '05:38'"""
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
    """Accetta '2026-07-25' o '25/07/2026' e restituisce '25/07/2026'."""
    if not value:
        return None
    value = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", value)
    if m:
        return value
    return value


# ---------------------------------------------------------------------------
# Rete
# ---------------------------------------------------------------------------
def fetch(url, retries=3, timeout=30):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    }
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  ! tentativo {attempt}/{retries} fallito: {e}")
            time.sleep(2 * attempt)
    raise RuntimeError(f"Impossibile scaricare {url}: {last_err}")


def build_search_url(search):
    """Costruisce la URL di ricerca da una voce di config.

    Se 'search_url' e' presente viene usata direttamente (metodo consigliato:
    basta copiare l'indirizzo dal sito). Altrimenti la si costruisce dai campi
    departure / arrival / date.
    """
    if search.get("search_url"):
        return search["search_url"]

    dep = search.get("departure", "")
    arr = search.get("arrival", "")
    date = search.get("date", "") or ""
    params = {
        "departure": dep,
        "departure_id": search.get("departure_id", dep),
        "arrival": arr,
        "arrival_id": search.get("arrival_id", arr),
        "date": date,
    }
    return BASE + SEARCH_PATH + "?" + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def card_root_for(link):
    """Risale dall'anchor 'Seleziona biglietto' fino al nodo piu' grande che
    contiene ancora UN SOLO biglietto: quello e' il riquadro della card.
    Indipendente dalle classi CSS, quindi resistente a modifiche del sito."""
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
    seg_text = "\n".join(seg_lines)
    seg_text = cut_at_terminators(seg_text)

    date_m = DATE_RE.search(seg_text)
    date = date_m.group(1) if date_m else None

    stops = []
    times = list(TIME_RE.finditer(seg_text))
    for k, m in enumerate(times):
        t = m.group(1)
        start = m.end()
        end = times[k + 1].start() if k + 1 < len(times) else len(seg_text)
        station = re.sub(r"\s+", " ", seg_text[start:end]).strip(" -–·\n\t")
        if station:
            stops.append((norm_time(t), station))
    return {"kind": kind, "date": date, "stops": stops}


def split_segments(lines):
    markers = [i for i, ln in enumerate(lines) if ln.strip().lower() in ("andata", "ritorno")]
    segments = []
    if not markers:
        segments.append(build_segment(lines, "andata"))
        return segments
    for k, idx in enumerate(markers):
        start = idx + 1
        end = markers[k + 1] if k + 1 < len(markers) else len(lines)
        segments.append(build_segment(lines[start:end], lines[idx].strip().lower()))
    return segments


def parse_card(node):
    raw = str(node)
    tm = TICKET_RE.search(raw)
    ticket_id = tm.group(1) if tm else None

    text = node.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    full = "\n".join(lines)

    pm = PRICE_RE.search(full)
    price = parse_price(pm.group(1)) if pm else None

    sm = SUMMARY_TIMES_RE.search(full)
    summary = (f"{sm.group(1)} → {sm.group(2)}") if sm else None

    seller_m = SELLER_RE.search(full)
    seller = seller_m.group(1).strip() if seller_m else None

    segments = split_segments(lines)

    return {
        "id": ticket_id,
        "price": price,
        "summary": summary,
        "seller": seller,
        "segments": segments,
        "link": f"{BASE}/payments/buyTrainTicket/{ticket_id}" if ticket_id else None,
    }


def parse_page(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    links = soup.select('a[href*="buyTrainTicket/"]')
    seen_ids = set()
    cards = []
    for link in links:
        node = card_root_for(link)
        card = parse_card(node)
        if card["id"] and card["id"] not in seen_ids:
            seen_ids.add(card["id"])
            cards.append(card)
    return cards


# ---------------------------------------------------------------------------
# Filtri
# ---------------------------------------------------------------------------
def segment_route_match(segment, dep_sub, arr_sub):
    """Ritorna (dep_time, arr_time) se nella tratta esiste una fermata di
    partenza (che contiene dep_sub) PRIMA di una fermata di arrivo (arr_sub)."""
    stops = segment["stops"]
    dep_sub = (dep_sub or "").lower()
    arr_sub = (arr_sub or "").lower()
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
        return {
            "kind": seg["kind"],
            "date": seg.get("date"),
            "dep_time": dep_time,
            "arr_time": arr_time,
        }
    return None


# ---------------------------------------------------------------------------
# Stato (biglietti gia' visti)
# ---------------------------------------------------------------------------
def load_state():
    if not os.path.exists(STATE_PATH):
        return None  # None = primo avvio
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        log(f"Stato illeggibile ({e}), riparto da zero.")
        return None


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    # purga gli ID piu' vecchi della retention
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=STATE_RETENTION_DAYS)).timestamp()
    seen = state.get("seen", {})
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    state["seen"] = seen
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def telegram_send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("ERRORE: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non impostati.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, data=payload, timeout=30)
        if r.status_code != 200:
            log(f"Telegram ha risposto {r.status_code}: {r.text}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log(f"Invio Telegram fallito: {e}")
        return False


def esc(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def format_message(card, match, search):
    name = search.get("name") or "Biglietto"
    lines = [f"\U0001F39F️ <b>Nuovo biglietto: {esc(name)}</b>"]
    route = []
    if search.get("match_departure_contains"):
        route.append(search["match_departure_contains"].title())
    if search.get("match_arrival_contains"):
        route.append(search["match_arrival_contains"].title())
    if route:
        lines.append("→ " + esc(" → ".join(route)))
    info = []
    if match.get("date"):
        info.append(f"\U0001F4C5 {esc(match['date'])}")
    if match.get("dep_time"):
        info.append(f"\U0001F552 {esc(match['dep_time'])} → {esc(match.get('arr_time',''))}")
    if info:
        lines.append(" · ".join(info))
    if card.get("price") is not None:
        lines.append(f"\U0001F4B6 {esc(int(card['price']) if card['price'].is_integer() else card['price'])} €")
    if card.get("seller"):
        lines.append(f"\U0001F464 {esc(card['seller'])}")
    if match.get("kind"):
        lines.append(f"<i>(corrispondenza sulla tratta di {esc(match['kind'])})</i>")
    if card.get("link"):
        lines.append(f"\n\U0001F517 <a href=\"{esc(card['link'])}\">Apri il biglietto</a>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Comandi Telegram (imposta le ricerche dalla chat)
# ---------------------------------------------------------------------------
HELP = (
    "\U0001F916 <b>Bot Trovaunposto</b>\n"
    "Comandi disponibili:\n\n"
    "<b>/lista</b> — mostra le ricerche attive\n"
    "<b>/aggiungi</b> PARTENZA &gt; ARRIVO [data] [oraInizio-oraFine] [maxPREZZO]\n"
    "<b>/rimuovi</b> N — elimina la ricerca numero N\n"
    "<b>/pausa</b> — sospende le notifiche\n"
    "<b>/riprendi</b> — riattiva le notifiche\n"
    "<b>/stato</b> — mostra lo stato\n"
    "<b>/aiuto</b> — questo messaggio\n\n"
    "Esempi:\n"
    "<code>/aggiungi Milano &gt; Roma 2026-07-25 17:00-21:00 max60</code>\n"
    "<code>/aggiungi Napoli &gt; Milano 25/12/2026</code>\n"
    "<code>/aggiungi Torino &gt; Roma 08:00-12:00</code>\n\n"
    "La data è facoltativa (AAAA-MM-GG oppure GG/MM/AAAA), così come orario e prezzo. "
    "Le città vengono cercate su tutte le stazioni. "
    "Le modifiche diventano attive entro pochi minuti."
)


def city_key(city):
    """Parola chiave per il filtro di direzione: 'Milano' -> 'MILANO'."""
    return city.strip().upper().split("(")[0].strip()


def to_station(city):
    """'Milano' -> 'MILANO(TUTTE LE STAZIONI)'. Se l'utente specifica gia'
    una stazione precisa (con parentesi) la lascia com'e'."""
    c = city.strip()
    if "(" in c:
        return c.upper()
    return f"{c.upper()}(TUTTE LE STAZIONI)"


def parse_date_token(tok):
    if re.match(r"^\d{4}-\d{2}-\d{2}$", tok):
        return tok
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", tok)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def parse_add(args):
    """Interpreta il testo di /aggiungi. Ritorna (search_dict, None) oppure
    (None, messaggio_di_errore)."""
    if ">" not in args:
        return None, ("Formato non valido. Usa '>' tra partenza e arrivo, es:\n"
                      "<code>/aggiungi Milano &gt; Roma 2026-07-25 17:00-21:00 max60</code>")
    left, right = args.split(">", 1)
    partenza = left.strip()
    if not partenza:
        return None, "Manca la stazione di partenza."

    arr_words, date, tfrom, tto, maxp = [], None, None, None, None
    for tok in right.split():
        d = parse_date_token(tok)
        if d:
            date = d
            continue
        tr = re.match(r"^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$", tok)
        if tr:
            tfrom, tto = norm_time(tr.group(1)), norm_time(tr.group(2))
            continue
        pm = (re.match(r"(?i)^max(\d+)$", tok)
              or re.match(r"^(\d+)\s*€$", tok)
              or re.match(r"^€\s*(\d+)$", tok))
        if pm:
            maxp = int(pm.group(1))
            continue
        arr_words.append(tok)

    arrivo = " ".join(arr_words).strip()
    if not arrivo:
        return None, "Manca la stazione di arrivo."

    dep_station, arr_station = to_station(partenza), to_station(arrivo)
    url = BASE + SEARCH_PATH + "?" + urllib.parse.urlencode({
        "departure": dep_station,
        "departure_id": dep_station,
        "arrival": arr_station,
        "arrival_id": arr_station,
        "date": date or "",
    })
    search = {
        "name": f"{partenza.title()} → {arrivo.title()}",
        "search_url": url,
        "match_departure_contains": city_key(partenza),
        "match_arrival_contains": city_key(arrivo),
        "match_date": date or "",
        "time_from": tfrom or "",
        "time_to": tto or "",
        "max_price": maxp,
    }
    return search, None


def format_search_human(s):
    base = s.get("name") or f"{s.get('match_departure_contains','?')} → {s.get('match_arrival_contains','?')}"
    extra = []
    if s.get("match_date"):
        extra.append(f"giorno {to_ddmmyyyy(s['match_date'])}")
    if s.get("time_from") or s.get("time_to"):
        extra.append(f"ore {s.get('time_from','')}-{s.get('time_to','')}")
    if s.get("max_price") is not None:
        extra.append(f"max {s['max_price']}€")
    text = base + (f" ({', '.join(extra)})" if extra else "")
    return esc(text)


def seed_search(search, seen, now_ts):
    """Registra come 'gia' visti' i biglietti che corrispondono ora, senza
    inviare notifiche (usato quando aggiungi una nuova ricerca)."""
    html_text = fetch(build_search_url(search))
    n = 0
    for card in parse_page(html_text):
        if ticket_matches(card, search):
            seen[card["id"]] = now_ts
            n += 1
    return n


def handle_command(text, cfg, state, now_ts):
    """Esegue un comando ricevuto via Telegram. Ritorna (risposta, cfg_modificata)."""
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    args = parts[1].strip() if len(parts) > 1 else ""
    searches = cfg.setdefault("searches", [])

    if cmd in ("start", "aiuto", "help"):
        return HELP, False

    if cmd == "lista":
        if not searches:
            return "Nessuna ricerca attiva. Aggiungine una con /aggiungi (vedi /aiuto).", False
        lines = ["<b>Ricerche attive:</b>"]
        for i, s in enumerate(searches, 1):
            lines.append(f"{i}. {format_search_human(s)}")
        return "\n".join(lines), False

    if cmd == "aggiungi":
        search, err = parse_add(args)
        if err:
            return err, False
        searches.append(search)
        try:
            n = seed_search(search, state["seen"], now_ts)
            extra = (f" Al momento ci sono {n} biglietti che corrispondono; "
                     "ti avviserò dei prossimi.")
        except Exception:  # noqa: BLE001
            extra = " (controllerò i biglietti al prossimo giro.)"
        return (f"✅ Aggiunta ricerca #{len(searches)}: "
                f"{format_search_human(search)}.{extra}"), True

    if cmd in ("rimuovi", "elimina", "cancella"):
        if not args.isdigit():
            return "Indica il numero della ricerca, es: /rimuovi 2 (vedi /lista).", False
        idx = int(args)
        if idx < 1 or idx > len(searches):
            return f"Numero non valido. Ho {len(searches)} ricerche (vedi /lista).", False
        removed = searches.pop(idx - 1)
        return f"\U0001F5D1 Rimossa la ricerca #{idx}: {format_search_human(removed)}.", True

    if cmd == "pausa":
        state["paused"] = True
        return "⏸ Notifiche sospese. Usa /riprendi per riattivarle.", False

    if cmd in ("riprendi", "riattiva"):
        state["paused"] = False
        return "▶️ Notifiche riattivate.", False

    if cmd == "stato":
        stato = "in pausa" if state.get("paused") else "attivo"
        return f"Stato: <b>{stato}</b>. Ricerche attive: {len(searches)}.", False

    return "Comando non riconosciuto. Scrivi /aiuto per la lista dei comandi.", False


def telegram_get_updates(offset, timeout=0):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(url, params=params, timeout=timeout + 30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"getUpdates ha risposto: {data}")
    return data.get("result", [])


def process_telegram(cfg, state, now_ts):
    """Legge i nuovi messaggi/comandi dalla chat dell'utente. Ritorna True se
    la configurazione (le ricerche) e' cambiata."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    updates = telegram_get_updates(state.get("tg_offset"))
    cfg_changed = False
    handled = 0
    for up in updates:
        state["tg_offset"] = up["update_id"] + 1
        msg = up.get("message") or up.get("edited_message")
        if not msg:
            continue
        # Accetta comandi SOLO dalla chat del proprietario (ignora estranei).
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        reply, changed = handle_command(text, cfg, state, now_ts)
        cfg_changed = cfg_changed or changed
        if reply:
            telegram_send(reply)
        handled += 1
    if updates:
        log(f"Comandi Telegram elaborati: {handled} (nuovo offset {state.get('tg_offset')}).")
    return cfg_changed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def run(dry_run=False, notify_all=False):
    cfg = load_config()

    state = load_state()
    first_run = state is None
    if state is None:
        state = {"seen": {}}
    state.setdefault("seen", {})
    seen = state["seen"]
    now_ts = dt.datetime.utcnow().timestamp()

    # 1) Leggi i comandi arrivati su Telegram (salta in dry-run locale).
    if not dry_run:
        try:
            if process_telegram(cfg, state, now_ts):
                save_config(cfg)
        except Exception as e:  # noqa: BLE001
            log(f"Lettura comandi Telegram fallita: {e}")

    searches = cfg.get("searches", [])
    if not searches:
        log("Nessuna ricerca configurata (aggiungine una con /aggiungi su Telegram).")
        if not dry_run:
            save_state(state)  # salva almeno offset comandi / pausa
        return 0

    notify_on_first_run = bool(cfg.get("notify_on_first_run", False)) or notify_all
    paused = bool(state.get("paused", False))

    to_notify = []  # (card, match, search)
    total_matches = 0

    for search in searches:
        name = search.get("name", "(senza nome)")
        url = build_search_url(search)
        log(f"Ricerca '{name}' -> {url}")
        try:
            html_text = fetch(url)
        except Exception as e:  # noqa: BLE001
            log(f"  ! errore di rete, salto: {e}")
            continue

        cards = parse_page(html_text)
        log(f"  trovati {len(cards)} biglietti in pagina")

        for card in cards:
            match = ticket_matches(card, search)
            if not match:
                continue
            total_matches += 1
            tid = card["id"]
            already = tid in seen
            if already and not notify_all:
                continue
            to_notify.append((card, match, search))
            seen[tid] = now_ts  # segna come visto

        time.sleep(1.0)  # gentile col sito

    log(f"Corrispondenze totali ai criteri: {total_matches}. Nuove da notificare: {len(to_notify)}.")

    # Primo avvio: di norma NON notifichiamo i biglietti gia' presenti, li
    # registriamo soltanto, cosi' eviti una valanga di messaggi iniziale.
    if first_run and not notify_on_first_run:
        log("Primo avvio: registro i biglietti attuali senza inviare notifiche "
            "(metti notify_on_first_run=true per cambiare).")
        for card, _m, _s in to_notify:
            seen[card["id"]] = now_ts
        to_notify = []

    if paused and not dry_run and to_notify:
        log("Notifiche in pausa: registro i biglietti senza inviare.")
        to_notify = []

    if dry_run:
        log("--- DRY RUN: nessun invio. Anteprima dei messaggi ---")
        for card, match, search in to_notify:
            print("\n" + format_message(card, match, search))
        print(f"\n[Totale nuovi: {len(to_notify)}]")
        return 0

    sent = 0
    for card, match, search in to_notify[:MAX_NOTIFICATIONS_PER_RUN]:
        if telegram_send(format_message(card, match, search)):
            sent += 1
            time.sleep(0.5)
    if len(to_notify) > MAX_NOTIFICATIONS_PER_RUN:
        telegram_send(
            f"… e altri {len(to_notify) - MAX_NOTIFICATIONS_PER_RUN} biglietti. "
            "Apri trovaunposto.it per vederli tutti."
        )
    log(f"Inviate {sent} notifiche.")

    save_state(state)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Bot di monitoraggio trovaunposto.it")
    ap.add_argument("--dry-run", action="store_true", help="non invia nulla, stampa soltanto")
    ap.add_argument("--test", action="store_true", help="invia un messaggio di prova su Telegram")
    ap.add_argument("--all", action="store_true", help="notifica anche i biglietti gia' visti")
    args = ap.parse_args()

    if args.test:
        ok = telegram_send("✅ Test Trovaunposto bot: la connessione Telegram funziona!")
        log("Messaggio di prova inviato." if ok else "Invio di prova fallito.")
        return 0 if ok else 1

    return run(dry_run=args.dry_run, notify_all=args.all)


if __name__ == "__main__":
    sys.exit(main())

# Bot Trovaunposto — fine file.
