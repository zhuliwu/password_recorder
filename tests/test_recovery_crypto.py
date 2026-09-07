import pytest

from vault.recovery import generate_recovery_key, parse_recovery_key


def test_recovery_key_round_trip_and_normalization():
    key, formatted = generate_recovery_key()
    assert len(key) == 32 and formatted.startswith("RK1-")
    assert parse_recovery_key(formatted) == key
    assert parse_recovery_key(formatted.lower().replace("-", " \n")) == key
    assert parse_recovery_key(key.hex()) == key
    assert generate_recovery_key()[0] != key


@pytest.mark.parametrize("value", [None, 123, [], "", "RK1-1234", "G" * 64, "A" * 63, "A" * 65, "A" * 201])
def test_invalid_recovery_key_format(value):
    with pytest.raises(ValueError):
        parse_recovery_key(value)
