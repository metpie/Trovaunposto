import json

import pytest

import trovaunposto_live as bot

OWNER = "1000"
NOW = 1_757_800_000.0
NOW_RECENT = bot.dt.datetime.utcnow().timestamp()


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


# --- Task 2: seen -------------------------------------------------------------

def test_seen_key():
    assert bot.seen_key("42", "987") == "42:987"
    assert bot.seen_key(42, 987) == "42:987"


def test_migrate_seen_prefixes_old_keys_with_owner():
    old = {"111": 1.0, "222": 2.0, "42:333": 3.0}
    new = bot.migrate_seen(old, OWNER)
    assert new == {"1000:111": 1.0, "1000:222": 2.0, "42:333": 3.0}
    assert "111" in old  # non modifica l'input


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


# --- Task 4: limiti e revoca ---------------------------------------------------

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


# --- Revisione finale ---

def test_iter_searches_skips_paused_users_when_only_active():
    store = bot.empty_store()
    bot.ensure_admin(store, OWNER, "Matteo", NOW)
    store["users"][OWNER]["searches"].append(_search("A"))
    store["users"]["42"] = bot.new_user("Anna", "guest", NOW)
    store["users"]["42"]["searches"].append(_search("B"))
    store["users"]["42"]["paused"] = True
    active = list(bot.iter_searches(store, only_active=True))
    assert [(u, s["name"]) for u, s in active] == [(OWNER, "A")]
    everything = list(bot.iter_searches(store, only_active=False))
    assert sorted((u, s["name"]) for u, s in everything) == [(OWNER, "A"), ("42", "B")]


def test_load_store_migrates_v1_file_on_disk(tmp_path, monkeypatch):
    path = tmp_path / "searches.json"
    path.write_text(json.dumps({"searches": [_search("Vecchia")], "paused": True}), encoding="utf-8")
    monkeypatch.setattr(bot, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "SEARCHES_PATH", str(path))
    store = bot.load_store()
    assert store["version"] == bot.STORE_VERSION
    assert store["users"][OWNER]["searches"][0]["name"] == "Vecchia"
    assert store["users"][OWNER]["paused"] is True
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == bot.STORE_VERSION  # riscritto in formato 2


def test_load_store_refuses_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "searches.json"
    path.write_text("{ non è json", encoding="utf-8")
    monkeypatch.setattr(bot, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "SEARCHES_PATH", str(path))
    with pytest.raises(SystemExit):
        bot.load_store()
    assert path.read_text(encoding="utf-8") == "{ non è json"  # non sovrascritto


def test_save_seen_prunes_in_place_and_returns_same_object(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bot, "SEEN_PATH", str(tmp_path / "seen.json"))
    old = (bot.dt.datetime.utcnow() - bot.dt.timedelta(days=bot.SEEN_RETENTION_DAYS + 1)).timestamp()
    seen = {"1000:1": old, "1000:2": NOW_RECENT}
    out = bot.save_seen(seen)
    assert out is seen
    assert list(seen) == ["1000:2"]


# --- Comandi registrati -------------------------------------------------------

def test_commands_for_roles():
    guest = [c for c, _ in bot.commands_for("guest")]
    admin = [c for c, _ in bot.commands_for("admin")]
    assert guest == ["cerca", "aggiungi", "lista", "pausa", "riprendi", "stato", "aiuto"]
    assert admin[:len(guest)] == guest
    assert set(admin) - set(guest) == {"invita", "utenti", "pulisci"}
    assert len(set(admin)) == len(admin)
    assert "debug" not in admin


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
    assert all(0 < len(d) <= 40 for _, d in bot.commands_for("admin"))
