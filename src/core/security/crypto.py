from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class EncryptionError(ValueError):
    pass


class SecurityCipher:
    def __init__(self, fernet_key: str) -> None:
        self._fernet = Fernet(fernet_key.encode("utf-8"))

    def encrypt(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode("utf-8"))
        except InvalidToken as exc:
            raise EncryptionError("Unable to decrypt value") from exc
        return plaintext.decode("utf-8")
