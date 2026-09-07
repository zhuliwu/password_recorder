"""256-bit recovery secrets wrap a vault key; plaintext secrets are never persisted."""
import os
import re

RECOVERY_CONTEXT = b"vault-recovery-key-v1"


def generate_recovery_key():
    key = os.urandom(32)
    encoded = key.hex().upper()
    return key, "RK1-" + "-".join(encoded[i:i + 8] for i in range(0, 64, 8))


def parse_recovery_key(value):
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("恢复密钥格式不正确")
    normalized = re.sub(r"[\s-]", "", value).upper()
    if normalized.startswith("RK1"):
        normalized = normalized[3:]
    if not re.fullmatch(r"[0-9A-F]{64}", normalized):
        raise ValueError("恢复密钥应为 RK1 开头的完整密钥")
    return bytes.fromhex(normalized)
