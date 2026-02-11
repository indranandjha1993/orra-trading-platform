from src.core.config import settings
from src.core.security.crypto import SecurityCipher


def get_security_cipher() -> SecurityCipher:
    key = (settings.master_encryption_key or "").strip()
    if not key or key == "replace_with_fernet_key":
        raise ValueError("MASTER_ENCRYPTION_KEY is not configured with a valid Fernet key")
    return SecurityCipher(key)
