"""Telegram Mini App sign-in: checking ``initData`` (PQ-048).

Telegram signs the data it hands to the Mini App with the bot's token
(https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
the secret is HMAC-SHA256 of the token with the key ``WebAppData``; the hash
is HMAC-SHA256 of the sorted ``key=value`` lines without ``hash``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

# How long one opening of the Mini App may keep working.
INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60
# Clocks of Telegram and the server may differ a little.
CLOCK_SKEW_SECONDS = 60


class InitDataError(Exception):
    """The data is not signed by this bot, is too old or broken."""


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""


def _expected_hash(pairs: list[tuple[str, str]], bot_token: str) -> str:
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()


def validate_init_data(
    init_data: str, bot_token: str, *, now: float | None = None
) -> TelegramUser:
    if not init_data or not bot_token:
        raise InitDataError("empty")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("broken") from exc
    received = [value for key, value in pairs if key == "hash"]
    pairs = [(key, value) for key, value in pairs if key != "hash"]
    if len(received) != 1 or not hmac.compare_digest(
        received[0], _expected_hash(pairs, bot_token)
    ):
        raise InitDataError("signature")
    fields = dict(pairs)
    try:
        auth_date = int(fields.get("auth_date", ""))
        user = json.loads(fields.get("user", ""))
        user_id = int(user["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InitDataError("fields") from exc
    now = time.time() if now is None else now
    if auth_date > now + CLOCK_SKEW_SECONDS or now - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise InitDataError("expired")
    return TelegramUser(
        id=user_id,
        first_name=str(user.get("first_name") or "")[:255],
        last_name=str(user.get("last_name") or "")[:255],
        username=str(user.get("username") or "")[:255],
        language_code=str(user.get("language_code") or "")[:16],
    )


def sign_init_data(fields: dict, bot_token: str) -> str:
    """Build signed ``initData`` the way Telegram does (tests and the emulator)."""

    from urllib.parse import urlencode

    pairs = [
        (key, value if isinstance(value, str) else json.dumps(value, separators=(",", ":")))
        for key, value in fields.items()
    ]
    return urlencode([*pairs, ("hash", _expected_hash(pairs, bot_token))])
