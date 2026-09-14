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


# --- Task 2: seen -------------------------------------------------------------

def test_seen_key():
    assert bot.seen_key("42", "987") == "42:987"
    assert bot.seen_key(42, 987) == "42:987"


def test_migrate_seen_prefixes_old_keys_with_owner():
    old = {"111": 1.0, "222": 2.0, "42:333": 3.0}
    new = bot.migrate_seen(old, OWNER)
    assert new == {"1000:111": 1.0, "1000:222": 2.0, "42:333": 3.0}
    assert "111" in old  # non modifica l'input
