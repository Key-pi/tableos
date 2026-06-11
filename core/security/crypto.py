from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _get_cipher() -> Fernet:
    key = settings.BOT_TOKEN_ENCRYPTION_KEY
    if not key:
        raise ImproperlyConfigured("BOT_TOKEN_ENCRYPTION_KEY must be configured.")
    return Fernet(key.encode("utf-8"))


def encrypt_text(value: str) -> str:
    return _get_cipher().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str) -> str:
    try:
        return _get_cipher().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ImproperlyConfigured("Unable to decrypt bot token. Check encryption key.") from exc
