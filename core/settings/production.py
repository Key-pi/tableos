"""Production settings (AA-038, docs/engineering/DEPLOYMENT.md).

Every process of a production deployment — web, worker, beat, bot, init —
runs with ``DJANGO_SETTINGS_MODULE=core.settings.production``. The module
refuses to load with values that are only safe on a developer's computer.
"""

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from core.settings.base import *  # noqa: F403
from core.settings.base import MIDDLEWARE
from core.settings.env import app_settings

INSECURE_SECRET_KEYS = {"", "unsafe-dev-secret-key", "change-me"}
INSECURE_DB_PASSWORDS = {"", "tableos", "postgres", "password"}


def _refuse(message: str) -> None:
    raise ImproperlyConfigured(f"Продакшен: {message}")


if app_settings.secret_key in INSECURE_SECRET_KEYS or len(app_settings.secret_key) < 50:
    _refuse(
        "DJANGO_SECRET_KEY не задан или слабый (нужно не меньше 50 случайных символов): "
        "python -c 'import secrets; print(secrets.token_urlsafe(64))'"
    )
try:
    Fernet(app_settings.bot_token_encryption_key.strip().encode())
except Exception:
    _refuse("BOT_TOKEN_ENCRYPTION_KEY пустой или некорректный (ключ Fernet).")
DEV_HOSTS = {"127.0.0.1", "localhost"}
if (
    not set(app_settings.allowed_hosts) - DEV_HOSTS
    or "*" in app_settings.allowed_hosts
):
    _refuse("DJANGO_ALLOWED_HOSTS — перечислите домены сайта через запятую, без «*».")
if app_settings.use_sqlite:
    _refuse("SQLite не для продакшена — DJANGO_USE_SQLITE=false и PostgreSQL.")
if app_settings.postgres_password in INSECURE_DB_PASSWORDS:
    _refuse("POSTGRES_PASSWORD не задан или стандартный — задайте свой пароль базы.")

DEBUG = False

# TLS is terminated by the reverse proxy in front of the web container; it
# must pass X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = app_settings.secure_hsts_seconds
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
# Subdomains and the browser preload list are decisions about the whole
# domain, not this service (DEPLOYMENT.md).
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 12
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

# Static files (Admin) are served by the web process itself.
MIDDLEWARE = [
    MIDDLEWARE[0],
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[1:],
]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Shared by all web workers: the Admin login throttle counts here.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": app_settings.redis_url,
    }
}
