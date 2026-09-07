import os
import sqlite3
import time

import pytest

from test_api import ENTRY, MASTER, call, setup, vault
from vault import create_app
from vault.recovery import generate_recovery_key, parse_recovery_key

NEW_MASTER = "a completely new master 987!"


def enable(client, master=MASTER):
    response = call(client, "/recovery/prepare", "POST", {"password": master})
    assert response.status_code == 200
    key = response.json["recovery_key"]
    assert call(client, "/recovery/confirm", "POST", {"recovery_key": key, "saved": True}).status_code == 200
    return key


def recover(client, key, password=NEW_MASTER):
    return call(client, "/recover", "POST", {"recovery_key": key, "new_password": password})


def snapshot(path):
    with sqlite3.connect(path / "vault.db") as db:
        return {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in ("entries", "metadata", "recovery")}


def test_recovery_requires_prior_enrollment(vault):
    _, client, _ = vault
    setup(client)
    assert call(client, "/recovery").json == {"enabled": False}
    key = generate_recovery_key()[1]
    assert recover(client, key).status_code == 409
    assert call(client, "/recovery/prepare", "POST", {"password": "wrong password!"}).status_code == 403
    assert call(client, "/status").json["unlocked"]
    call(client, "/lock", "POST", {})
    assert call(client, "/recovery/prepare", "POST", {"password": MASTER}).status_code == 401
    assert call(client, "/recovery/confirm", "POST", {"recovery_key": key, "saved": True}).status_code == 401


def test_prepare_confirm_cancel_expiry_and_no_plaintext(vault):
    app, client, path = vault
    setup(client)
    key = call(client, "/recovery/prepare", "POST", {"password": MASTER}).json["recovery_key"]
    assert call(client, "/recovery").json == {"enabled": False}
    assert recover(client, key).status_code == 409
    assert call(client, "/recovery/confirm", "POST", {"recovery_key": key, "saved": False}).status_code == 400
    assert call(client, "/recovery/confirm", "POST", {"recovery_key": generate_recovery_key()[1], "saved": True}).status_code == 400
    for _ in range(2):  # Confirmation can be retried if the first response was lost.
        assert call(client, "/recovery/confirm", "POST", {"recovery_key": key, "saved": True}).status_code == 200
    assert call(client, "/recovery").json == {"enabled": True}
    candidate = call(client, "/recovery/prepare", "POST", {"password": MASTER}).json["recovery_key"]
    call(client, "/recovery/cancel", "POST", {})
    assert call(client, "/recovery/confirm", "POST", {"recovery_key": candidate, "saved": True}).status_code == 409
    candidate = call(client, "/recovery/prepare", "POST", {"password": MASTER}).json["recovery_key"]
    for active in app.extensions["vault_sessions"].values():
        active["pending_recovery"]["expires"] = time.monotonic() - 1
    assert call(client, "/recovery/confirm", "POST", {"recovery_key": candidate, "saved": True}).status_code == 409
    raw = (path / "vault.db").read_bytes()
    assert parse_recovery_key(key) not in raw and key.encode() not in raw
    assert recover(client, key).status_code == 200


def test_recovery_after_restart_rotates_credentials_and_preserves_data(vault):
    _, client, path = vault
    setup(client)
    ids = [call(client, "/entries", "POST", {**ENTRY, "title": f"account {i}"}).json["id"] for i in range(3)]
    original = [call(client, f"/entries/{id}").json for id in ids]
    key = enable(client)
    app = create_app(path)
    old_session = app.test_client()
    assert call(old_session, "/unlock", "POST", {"password": MASTER}).status_code == 200
    fresh = app.test_client()
    assert recover(fresh, key.lower().replace("-", " \n")).status_code == 200
    assert call(old_session, "/entries").status_code == 401
    assert [call(fresh, f"/entries/{id}").json for id in ids] == original
    assert call(fresh, "/recovery").json == {"enabled": False}
    assert recover(fresh, key, "different master 321!").status_code == 409
    call(fresh, "/lock", "POST", {})
    assert call(fresh, "/unlock", "POST", {"password": MASTER}).status_code == 401
    assert call(fresh, "/unlock", "POST", {"password": NEW_MASTER}).status_code == 200
    another = create_app(path).test_client()
    assert call(another, "/unlock", "POST", {"password": NEW_MASTER}).status_code == 200
    assert call(another, f"/entries/{ids[0]}").json == original[0]
    replacement = enable(another, NEW_MASTER)
    assert recover(another, replacement, "third master password!").status_code == 200
    raw = (path / "vault.db").read_bytes()
    for secret in (MASTER, NEW_MASTER, key, ENTRY["password"]):
        assert secret.encode() not in raw


def test_replacement_invalidates_old_key_only_after_confirmation(vault):
    _, client, _ = vault
    setup(client)
    old_key = enable(client)
    new_key = enable(client)
    assert recover(client, old_key).status_code == 401
    assert recover(client, new_key).status_code == 200


@pytest.mark.parametrize("failure", ["corrupt_record", "database_write"])
def test_failed_recovery_rolls_back_entire_database(vault, failure):
    _, client, path = vault
    setup(client)
    for i in range(2):
        call(client, "/entries", "POST", {**ENTRY, "title": str(i)})
    key = enable(client)
    with sqlite3.connect(path / "vault.db") as db:
        if failure == "corrupt_record":
            last = db.execute("SELECT id FROM entries ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            db.execute("UPDATE entries SET payload=? WHERE id=?", (os.urandom(50), last))
        else:
            db.execute("CREATE TRIGGER fail_reset BEFORE UPDATE ON metadata BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    before = snapshot(path)
    assert recover(client, key).status_code == (500 if failure == "corrupt_record" else 503)
    assert snapshot(path) == before
    call(client, "/lock", "POST", {})
    assert call(client, "/unlock", "POST", {"password": MASTER}).status_code == 200


def test_invalid_recovery_and_rate_limit_do_not_change_data(vault):
    _, client, path = vault
    setup(client)
    key = enable(client)
    before = snapshot(path)
    assert recover(client, key, "short").status_code == 400
    assert recover(client, "invalid").status_code == 400
    for _ in range(5):
        assert recover(client, generate_recovery_key()[1]).status_code == 401
    assert recover(client, key).status_code == 429
    assert snapshot(path) == before


def test_legacy_database_upgrade_preserves_encrypted_records(vault):
    _, client, path = vault
    setup(client)
    entry_id = call(client, "/entries", "POST", ENTRY).json["id"]
    with sqlite3.connect(path / "vault.db") as db:
        db.execute("DROP TABLE recovery")
        before = db.execute("SELECT * FROM entries").fetchall()
    fresh = create_app(path).test_client()
    with sqlite3.connect(path / "vault.db") as db:
        assert db.execute("SELECT * FROM entries").fetchall() == before
    assert call(fresh, "/unlock", "POST", {"password": MASTER}).status_code == 200
    assert call(fresh, "/recovery").json == {"enabled": False}
    assert recover(fresh, enable(fresh)).status_code == 200
    assert call(fresh, f"/entries/{entry_id}").json["password"] == ENTRY["password"]


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"X-Vault-Request": ""}, {"Host": "evil.example"}])
def test_recovery_cannot_bypass_request_protection(vault, headers):
    _, client, _ = vault
    assert call(client, "/recover", "POST", {"recovery_key": generate_recovery_key()[1], "new_password": NEW_MASTER}, headers).status_code == 403
