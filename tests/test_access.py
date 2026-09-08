import pytest

from vault.access import normalize_public_origin, waitress_options
from vault.proxy_config import render_nginx


@pytest.mark.parametrize("value,expected", [
    (None, None), ("https://139.224.25.251", "https://139.224.25.251"),
    ("https://139.224.25.251:443/", "https://139.224.25.251"),
    ("https://139.224.25.251:8443", "https://139.224.25.251:8443"),
    ("https://VAULT.example.com/", "https://vault.example.com"),
    ("https://[2001:db8::1]:8443", "https://[2001:db8::1]:8443"),
])
def test_origin_normalization(value, expected):
    assert normalize_public_origin(value) == expected


@pytest.mark.parametrize("value", [
    "", "http://139.224.25.251", "139.224.25.251", "https://*", "https://example.com/app",
    "https://user:secret@example.com", "https://example.com?query", "https://example.com#fragment",
    "https://example.com:0", "https://example.com:99999", "https://example.com:",
    "https://example.com:/",
    "https://bad host", "https://example.com\n", "https://[invalid", "https://999.999.999.999",
    "https://example.com;include", "https://example.com.", "https://[fe80::1%eth0]",
])
def test_unsafe_origins_rejected(value):
    with pytest.raises(ValueError):
        normalize_public_origin(value)


def test_proxy_trust_is_explicit_and_loopback_only():
    local = waitress_options()
    assert "trusted_proxy" not in local and local["host"] == "127.0.0.1"
    public = waitress_options("https://139.224.25.251")
    assert public["host"] == public["trusted_proxy"] == "127.0.0.1"
    assert public["trusted_proxy_headers"] == {"x-forwarded-proto"}
    assert public["trusted_proxy_count"] == 1 and public["clear_untrusted_proxy_headers"]


def test_proxy_configuration_matches_custom_origin_and_ports():
    conf = render_nginx("https://139.224.25.251:8443", "/etc/vault/fullchain.pem", "/etc/vault/privkey.pem", 9876, 8080)
    assert "listen 8443 ssl default_server;" in conf and "listen 8080;" in conf
    assert 'proxy_set_header Host "139.224.25.251:8443";' in conf
    assert "return 308 https://139.224.25.251:8443$request_uri;" in conf
    assert "proxy_set_header X-Forwarded-Proto https;" in conf
    assert 'proxy_set_header Forwarded "";' in conf
    assert "proxy_pass http://127.0.0.1:9876;" in conf
    assert "proxy_buffering off;" in conf and "proxy_request_buffering off;" in conf
    assert "limit_req_status 429;" in conf
    assert "location ^~ /.well-known/acme-challenge/" in conf


@pytest.mark.parametrize("path", ["relative.pem", "/tmp/bad;include.conf", "/tmp/$host.pem", "/tmp/bad\n.pem"])
def test_nginx_path_injection_rejected(path):
    with pytest.raises(ValueError):
        render_nginx("https://139.224.25.251", path, "/etc/vault/key.pem")


def test_nginx_port_collision_rejected():
    with pytest.raises(ValueError):
        render_nginx("https://139.224.25.251:8765", "/tmp/cert.pem", "/tmp/key.pem")
