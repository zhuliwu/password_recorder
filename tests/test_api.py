import os
import sqlite3
import time

import pytest

from vault import create_app

BASE = "http://127.0.0.1:8765"
MASTER = "test master password 123!"
ENTRY = {"title": "私人邮箱", "username": "private@example.com", "password": "original-secret-value",
         "url": "https://example.com", "category": "工作", "notes": "private note", "favorite": True}


def call(client, path, method="GET", data=None, headers=None):
    return client.open("/api" + path, method=method, base_url=BASE, json=data,
                       headers={"Origin": BASE, "X-Vault-Request": "1", **(headers or {})})


@pytest.fixture
def vault(tmp_path):
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    return app, client, tmp_path


def setup(client):
    response = call(client, "/setup", "POST", {"password": MASTER})
    assert response.status_code == 200
    return response


def test_setup_lock_unlock_and_no_plaintext(vault):
    app, client, path = vault
    assert call(client, "/status").json["initialized"] is False
    assert call(client, "/entries").status_code == 401
    cookie = setup(client).headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and MASTER not in cookie
    assert call(client, "/setup", "POST", {"password": MASTER}).status_code == 409
    entry_id = call(client, "/entries", "POST", ENTRY).json["id"]
    assert call(client, "/lock", "POST", {}).status_code == 200
    assert not app.extensions["vault_sessions"]
    assert call(client, "/entries").status_code == 401
    assert call(client, "/unlock", "POST", {"password": "wrong master password"}).status_code == 401
    assert call(client, "/unlock", "POST", {"password": MASTER}).status_code == 200
    assert call(client, f"/entries/{entry_id}").json["password"] == ENTRY["password"]
    raw = (path / "vault.db").read_bytes()
    for secret in [MASTER, *[v for v in ENTRY.values() if isinstance(v, str)]]:
        assert secret.encode() not in raw
    assert os.stat(path / "vault.db").st_mode & 0o777 == 0o600
    assert os.stat(path).st_mode & 0o777 == 0o700


def test_crud_and_restart_persistence(vault):
    app, client, path = vault
    setup(client)
    created = call(client, "/entries", "POST", ENTRY)
    assert created.status_code == 201
    entry_id = created.json["id"]
    listing = call(client, "/entries").json["entries"]
    assert len(listing) == 1 and "password" not in listing[0]
    revised = {**ENTRY, "title": "新名称", "password": "changed-secret", "favorite": False}
    assert call(client, f"/entries/{entry_id}", "PUT", revised).status_code == 200
    # A new application instance represents a stopped/restarted process and loses sessions.
    fresh_client = create_app(path).test_client()
    assert call(fresh_client, "/entries").status_code == 401
    assert call(fresh_client, "/unlock", "POST", {"password": MASTER}).status_code == 200
    record = call(fresh_client, f"/entries/{entry_id}").json
    assert all(record[k] == v for k, v in revised.items())
    assert call(fresh_client, f"/entries/{entry_id}", "DELETE", {}).status_code == 200
    assert call(fresh_client, f"/entries/{entry_id}").status_code == 404
    assert call(fresh_client, f"/entries/{entry_id}", "DELETE", {}).status_code == 404
    assert call(fresh_client, "/entries").json == {"entries": []}


@pytest.mark.parametrize("headers,expected", [
    ({"Origin": "https://evil.example"}, 403), ({"Origin": "null"}, 403),
    ({"Origin": "http://localhost:8765"}, 403), ({"X-Vault-Request": ""}, 403),
    ({"Host": "evil.example"}, 403), ({"Host": "127.0.0.1:9999"}, 403),
])
def test_request_protection(vault, headers, expected):
    _, client, _ = vault
    assert call(client, "/setup", "POST", {"password": MASTER}, headers).status_code == expected
    assert not call(client, "/status").json["initialized"]


def test_json_validation_and_security_headers(vault):
    _, client, _ = vault
    assert client.post("/api/setup", base_url=BASE, data="password=test", headers={"Origin": BASE}).status_code == 415
    for payload in ([], "string", None):
        assert call(client, "/setup", "POST", payload).status_code in (400, 415)
    assert call(client, "/setup", "POST", {"password": "short"}).status_code == 400
    assert call(client, "/setup", "POST", {"password": "x" * 70000}).status_code == 413
    response = client.get("/", base_url=BASE)
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_timeout_and_second_session_invalidation(vault):
    app, client, _ = vault
    setup(client)
    other = app.test_client()
    assert call(other, "/unlock", "POST", {"password": MASTER}).status_code == 200
    assert call(client, "/entries").status_code == 401
    for active in app.extensions["vault_sessions"].values():
        active["last"] = time.monotonic() - 901
    assert call(other, "/touch", "POST", {}).status_code == 401
    assert call(other, "/status").json["unlocked"] is False


def test_unlock_rate_limit(vault):
    _, client, _ = vault
    setup(client)
    call(client, "/lock", "POST", {})
    for _ in range(5):
        assert call(client, "/unlock", "POST", {"password": "incorrect password"}).status_code == 401
    assert call(client, "/unlock", "POST", {"password": MASTER}).status_code == 429


def test_tamper_is_error_not_empty_list(vault):
    _, client, path = vault
    setup(client)
    entry_id = call(client, "/entries", "POST", ENTRY).json["id"]
    with sqlite3.connect(path / "vault.db") as db:
        db.execute("UPDATE entries SET payload=? WHERE id=?", (os.urandom(50), entry_id))
    response = call(client, "/entries")
    assert response.status_code == 500 and "完整性" in response.json["error"]


def test_generate_and_invalid_update(vault):
    _, client, _ = vault
    setup(client)
    password = call(client, "/generate").json["password"]
    assert len(password) == 20
    assert any(c.isdigit() for c in password) and any(c.isupper() for c in password)
    entry_id = call(client, "/entries", "POST", ENTRY).json["id"]
    assert call(client, f"/entries/{entry_id}", "PUT", {**ENTRY, "password": ""}).status_code == 400
    assert call(client, f"/entries/{entry_id}").json["password"] == ENTRY["password"]


def test_idle_timer_discards_key_without_another_request(vault):
    app, client, _ = vault
    app.config["IDLE_SECONDS"] = 0.1
    setup(client)
    deadline = time.monotonic() + 2
    while app.extensions["vault_sessions"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not app.extensions["vault_sessions"]
