"""Authenticated encryption; plaintext keys are never persisted."""
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


def derive_key(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode())


def encrypt(key: bytes, plaintext: bytes, context: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, context)


def decrypt(key: bytes, ciphertext: bytes, context: bytes) -> bytes:
    return AESGCM(key).decrypt(ciphertext[:12], ciphertext[12:], context)
