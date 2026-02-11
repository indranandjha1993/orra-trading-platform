from __future__ import annotations

from cryptography.fernet import Fernet
import pytest

from src.core.security.crypto import EncryptionError, SecurityCipher
from src.core.security.dependencies import get_security_cipher


def test_security_cipher_roundtrip() -> None:
    key = Fernet.generate_key().decode()
    cipher = SecurityCipher(key)

    secret = "kite-api-secret"
    encrypted = cipher.encrypt(secret)

    assert encrypted != secret
    assert cipher.decrypt(encrypted) == secret


def test_security_cipher_invalid_token_raises() -> None:
    key = Fernet.generate_key().decode()
    cipher = SecurityCipher(key)

    with pytest.raises(EncryptionError):
        cipher.decrypt("not-a-valid-token")


def test_get_security_cipher_requires_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.security import dependencies

    monkeypatch.setattr(dependencies.settings, "master_encryption_key", "")
    with pytest.raises(ValueError, match="MASTER_ENCRYPTION_KEY"):
        get_security_cipher()

    monkeypatch.setattr(dependencies.settings, "master_encryption_key", "replace_with_fernet_key")
    with pytest.raises(ValueError, match="MASTER_ENCRYPTION_KEY"):
        get_security_cipher()

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(dependencies.settings, "master_encryption_key", key)

    cipher = get_security_cipher()
    assert isinstance(cipher, SecurityCipher)
