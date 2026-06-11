import sys
from pathlib import Path

from core.logging.config import LOGGING
from core.settings.env import app_settings

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = app_settings.secret_key
DEBUG = app_settings.debug
ALLOWED_HOSTS = app_settings.allowed_hosts
CSRF_TRUSTED_ORIGINS = app_settings.csrf_trusted_origins

INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bot.apps.BotConfig",
    "apps.partners.apps.PartnersConfig",
    "apps.users.apps.UsersConfig",
    "apps.tables.apps.TablesConfig",
    "apps.menu.apps.MenuConfig",
    "apps.orders.apps.OrdersConfig",
    "apps.billing.apps.BillingConfig",
    "apps.bonuses.apps.BonusesConfig",
    "apps.employees.apps.EmployeesConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.analytics.apps.AnalyticsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"
WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {"default": app_settings.database_config}

AUTH_USER_MODEL = "users.User"

LANGUAGE_CODE = app_settings.language_code
TIME_ZONE = app_settings.timezone
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGGING = LOGGING

UNFOLD = {
    "SITE_TITLE": "tableos",
    "SITE_HEADER": "tableos admin",
    "SITE_SYMBOL": "table_restaurant",
}

BOT_TOKEN_ENCRYPTION_KEY = app_settings.bot_token_encryption_key
REDIS_URL = app_settings.redis_url

CELERY_BROKER_URL = app_settings.celery_broker_url or app_settings.redis_url
CELERY_RESULT_BACKEND = app_settings.celery_result_backend or app_settings.redis_url
CELERY_TASK_ALWAYS_EAGER = app_settings.celery_task_always_eager or "test" in sys.argv
CELERY_TASK_EAGER_PROPAGATES = app_settings.celery_task_eager_propagates
CELERY_TASK_IGNORE_RESULT = app_settings.celery_task_ignore_result
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
