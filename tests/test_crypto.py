import os

import pytest
from cryptography.exceptions import InvalidTag

from vault.crypto import decrypt, derive_key, encrypt
from vault import APIError, validate_entry


def test_authenticated_encryption():
    key = os.urandom(32)
    message = "密码和账号".encode()
    first, second = encrypt(key, message, b"one"), encrypt(key, message, b"one")
    assert first != second and message not in first
    assert decrypt(key, first, b"one") == message
    for wrong_key, data, aad in [(os.urandom(32), first, b"one"), (key, first, b"two"),
                                  (key, first[:-1] + bytes([first[-1] ^ 1]), b"one")]:
        with pytest.raises(InvalidTag):
            decrypt(wrong_key, data, aad)


def test_key_derivation_is_salted_and_deterministic():
    salt = os.urandom(16)
    key = derive_key("long master password", salt)
    assert key == derive_key("long master password", salt)
    assert key != derive_key("long master password", os.urandom(16))


@pytest.mark.parametrize("changes", [
    {"title": " "}, {"username": ""}, {"password": ""}, {"password": 123},
    {"favorite": "yes"}, {"category": "invalid"}, {"title": "x" * 121},
    {"url": "javascript:alert(1)"}, {"url": "https://a:b@example.com"},
    {"url": "http://[invalid"}, {"extra": "field"},
    {"url": "https://exa mple.com"}, {"url": "https://example.com:99999"},
])
def test_invalid_fields_rejected(changes):
    with pytest.raises(APIError):
        validate_entry({"title": "邮箱", "username": "a@b.com", "password": "secret", **changes})


def test_password_whitespace_preserved():
    entry = validate_entry({"title": " 邮箱 ", "username": " user ", "password": " secret "})
    assert entry["password"] == " secret " and entry["title"] == "邮箱"
