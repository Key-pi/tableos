"""Production settings refuse unsafe values; Admin login is throttled (AA-038)."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.users.models import User

REPO = Path(__file__).resolve().parents[1]
GOOD_KEY = "k" * 20 + "Zx9!q" * 8
FERNET = "x" * 43 + "="


def _load_production(**env) -> subprocess.CompletedProcess:
    base = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO),
        "DJANGO_SETTINGS_MODULE": "core.settings.production",
        "DJANGO_SECRET_KEY": GOOD_KEY,
        "BOT_TOKEN_ENCRYPTION_KEY": FERNET,
        "DJANGO_ALLOWED_HOSTS": "shop.example.com",
        "POSTGRES_PASSWORD": "a-real-password",
    }
    base.update(env)
    with tempfile.TemporaryDirectory() as empty_dir:  # no .env is read here
        return subprocess.run(
            [sys.executable, "-c", "import django; django.setup(); print('loaded')"],
            cwd=empty_dir,
            env={key: value for key, value in base.items() if value is not None},
            capture_output=True,
            text=True,
        )


class ProductionSettingsTests(TestCase):
    def test_good_values_load(self):
        result = _load_production()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("loaded", result.stdout)

    def test_unsafe_values_are_refused(self):
        for env, message in (
            ({"DJANGO_SECRET_KEY": None}, "DJANGO_SECRET_KEY"),
            ({"DJANGO_SECRET_KEY": "change-me"}, "DJANGO_SECRET_KEY"),
            ({"BOT_TOKEN_ENCRYPTION_KEY": None}, "BOT_TOKEN_ENCRYPTION_KEY"),
            ({"DJANGO_ALLOWED_HOSTS": None}, "DJANGO_ALLOWED_HOSTS"),
            ({"DJANGO_ALLOWED_HOSTS": "*"}, "DJANGO_ALLOWED_HOSTS"),
            ({"POSTGRES_PASSWORD": None}, "POSTGRES_PASSWORD"),
            ({"DJANGO_USE_SQLITE": "true"}, "SQLite"),
        ):
            with self.subTest(env=env):
                result = _load_production(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_deploy_check_has_no_warnings(self):
        with tempfile.TemporaryDirectory() as empty_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "manage.py"),
                    "check",
                    "--deploy",
                    "--fail-level",
                    "WARNING",
                ],
                cwd=empty_dir,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": str(REPO),
                    "DJANGO_SETTINGS_MODULE": "core.settings.production",
                    "DJANGO_SECRET_KEY": GOOD_KEY,
                    "BOT_TOKEN_ENCRYPTION_KEY": FERNET,
                    "DJANGO_ALLOWED_HOSTS": "shop.example.com",
                    "POSTGRES_PASSWORD": "a-real-password",
                },
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class AdminLoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_superuser(username="root", password="right-pass-123", email="r@x.io")

    def _login(self, password, username="root"):
        return self.client.post(
            "/admin/login/", {"username": username, "password": password, "next": "/admin/"}
        )

    def test_five_failures_lock_the_account_for_a_while(self):
        for _ in range(5):
            self.assertEqual(self._login("wrong").status_code, 200)
        locked = self._login("right-pass-123")
        self.assertEqual(locked.status_code, 429)
        self.assertIn("15 минут", locked.content.decode())
        # Another account is not affected.
        User.objects.create_superuser(username="root2", password="other-pass-123", email="s@x.io")
        self.assertEqual(self._login("other-pass-123", username="root2").status_code, 302)

    def test_success_resets_the_counter(self):
        for _ in range(4):
            self._login("wrong")
        self.assertEqual(self._login("right-pass-123").status_code, 302)
        self.client.logout()
        for _ in range(4):
            self._login("wrong")
        self.assertEqual(self._login("right-pass-123").status_code, 302)

    def test_username_case_does_not_bypass_the_lock(self):
        for _ in range(5):
            self._login("wrong", username="ROOT")
        self.assertEqual(self._login("right-pass-123").status_code, 429)


class DemoDataRefusedInProductionTests(TestCase):
    def test_seed_commands_refuse_production(self):
        from django.core.management import CommandError, call_command

        with override_settings(SETTINGS_MODULE="core.settings.production"):
            for command in ("seed_baseline_demo", "seed_demo"):
                with self.subTest(command=command), self.assertRaisesMessage(
                    CommandError, "продакшене"
                ):
                    call_command(command)
        self.assertFalse(User.objects.filter(is_superuser=True).exists())
