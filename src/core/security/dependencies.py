from src.core.config import settings
from src.core.security.crypto import SecurityCipher


def get_security_cipher() -> SecurityCipher:
    return SecurityCipher(settings.master_encryption_key)
