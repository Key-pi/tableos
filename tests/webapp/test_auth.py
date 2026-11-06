"""Checking Telegram ``initData`` of the Mini App (PQ-048)."""

import hashlib
import hmac
import json
from urllib.parse import quote

from django.test import SimpleTestCase

from webapp.auth import InitDataError, sign_init_data, validate_init_data

TOKEN = "123456:TEST-token-for-mini-app"
NOW = 1_800_000_000


def independent_init_data(token: str, auth_date: int) -> str:
    """Signed by hand, step by step as Telegram describes it."""

    user = json.dumps({"id": 42, "first_name": "Оля", "language_code": "uk"}, ensure_ascii=False)
    fields = {"auth_date": str(auth_date), "query_id": "AAH", "user": user}
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return "&".join(f"{key}={quote(value)}" for key, value in fields.items()) + f"&hash={digest}"


class InitDataTests(SimpleTestCase):
    def test_data_signed_by_the_bot_is_accepted(self):
        user = validate_init_data(independent_init_data(TOKEN, NOW - 10), TOKEN, now=NOW)
        self.assertEqual((user.id, user.first_name, user.language_code), (42, "Оля", "uk"))

    def test_another_bot_or_a_changed_field_is_refused(self):
        data = independent_init_data(TOKEN, NOW)
        with self.assertRaises(InitDataError):
            validate_init_data(data, "999:other-bot", now=NOW)
        with self.assertRaises(InitDataError):
            validate_init_data(data.replace("%22id%22%3A%2042", "%22id%22%3A%2043"), TOKEN, now=NOW)
        with self.assertRaises(InitDataError):
            validate_init_data(data.split("&hash=")[0], TOKEN, now=NOW)

    def test_old_or_future_data_is_refused(self):
        with self.assertRaises(InitDataError):
            validate_init_data(independent_init_data(TOKEN, NOW - 2 * 86400), TOKEN, now=NOW)
        with self.assertRaises(InitDataError):
            validate_init_data(independent_init_data(TOKEN, NOW + 3600), TOKEN, now=NOW)

    def test_broken_data_is_refused(self):
        for data in ("", "hash=abc", "a=1&a", sign_init_data({"auth_date": str(NOW)}, TOKEN)):
            with self.assertRaises(InitDataError, msg=data):
                validate_init_data(data, TOKEN, now=NOW)

    def test_the_test_signer_matches_telegram(self):
        data = sign_init_data({"auth_date": str(NOW), "user": {"id": 7}}, TOKEN)
        self.assertEqual(validate_init_data(data, TOKEN, now=NOW).id, 7)
