import pytest
from waitress.proxy_headers import proxy_headers_middleware
from werkzeug.test import Client

from test_api import ENTRY, MASTER, setup
from vault import create_app

ORIGIN = "https://139.224.25.251"


def call(client, path, method="GET", data=None, headers=None, remote="127.0.0.1", host="139.224.25.251"):
    return client.open("/api" + path, method=method, base_url="http://" + host, json=data,
                       environ_overrides={"REMOTE_ADDR": remote},
                       headers={"Origin": ORIGIN, "X-Vault-Request": "1", "X-Forwarded-Proto": "https", **(headers or {})})


@pytest.fixture
def public_vault(tmp_path):
    local = create_app(tmp_path)
    setup(local.test_client())
    app = create_app(tmp_path, public_origin=ORIGIN)
    # Exercise the same header middleware and trust options used by Waitress.
    client = Client(proxy_headers_middleware(app, trusted_proxy="127.0.0.1", trusted_proxy_count=1,
                                            trusted_proxy_headers={"x-forwarded-proto"}, clear_untrusted=True))
    return app, client


def test_public_bootstrap_is_disabled(tmp_path):
    with pytest.raises(ValueError, match="先用本地模式"):
        create_app(tmp_path, public_origin=ORIGIN)


def test_https_crud_secure_cookie_and_logout(public_vault):
    _, client = public_vault
    assert call(client, "/entries").status_code == 401
    response = call(client, "/unlock", "POST", {"password": MASTER})
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("__Host-vault_session=")
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"))
    assert "Domain=" not in cookie
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert call(client, "/setup", "POST", {"password": MASTER}).status_code == 403
    entry_id = call(client, "/entries", "POST", ENTRY).json["id"]
    assert call(client, f"/entries/{entry_id}").json["password"] == ENTRY["password"]
    assert call(client, f"/entries/{entry_id}", "PUT", {**ENTRY, "title": "remote"}).status_code == 200
    assert call(client, f"/entries/{entry_id}", "DELETE", {}).status_code == 200
    response = call(client, "/lock", "POST", {})
    assert response.status_code == 200 and "Secure" in response.headers["Set-Cookie"]
    assert call(client, "/entries").status_code == 401


@pytest.mark.parametrize("headers,remote,host", [
    ({"X-Forwarded-Proto": "http"}, "127.0.0.1", "139.224.25.251"),
    ({"X-Forwarded-Proto": ""}, "127.0.0.1", "139.224.25.251"),
    ({}, "198.51.100.1", "139.224.25.251"),
    ({"Origin": "http://139.224.25.251"}, "127.0.0.1", "139.224.25.251"),
    ({"Origin": "https://evil.example"}, "127.0.0.1", "139.224.25.251"),
    ({"Origin": "null"}, "127.0.0.1", "139.224.25.251"),
    ({"X-Forwarded-Host": "139.224.25.251"}, "127.0.0.1", "evil.example"),
    ({}, "127.0.0.1", "127.0.0.1:8765"),
])
def test_public_origin_and_proxy_spoofing_rejected(public_vault, headers, remote, host):
    _, client = public_vault
    assert call(client, "/unlock", "POST", {"password": MASTER}, headers, remote, host).status_code == 403


def test_forwarded_headers_cannot_override_configured_origin(public_vault):
    _, client = public_vault
    response = call(client, "/unlock", "POST", {"password": MASTER}, {
        "Forwarded": "for=1.2.3.4;host=evil.example;proto=http",
        "X-Forwarded-Host": "evil.example", "X-Forwarded-Port": "1234", "X-Forwarded-For": "1.2.3.4",
    })
    assert response.status_code == 200
    assert "Secure" in response.headers["Set-Cookie"]


def test_nondefault_https_port_is_enforced(tmp_path):
    setup(create_app(tmp_path).test_client())
    app = create_app(tmp_path, public_origin=ORIGIN + ":8443")
    client = app.test_client()
    origin = ORIGIN + ":8443"
    response = client.post("/api/unlock", base_url=origin, json={"password": MASTER},
                           headers={"Origin": origin, "X-Vault-Request": "1"})
    assert response.status_code == 200
    assert client.get("/api/status", base_url=ORIGIN).status_code == 403
