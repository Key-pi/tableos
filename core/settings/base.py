import sys
from pathlib import Path

from core.admin_ui import get_admin_sidebar_navigation
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
STATICFILES_DIRS = [BASE_DIR / "static"]
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
    "SITE_TITLE": "TableOS Control Room",
    "SITE_HEADER": "TableOS Control Room",
    "SITE_SUBHEADER": "partner operations and bot content",
    "SITE_SYMBOL": "table_restaurant",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SHOW_BACK_BUTTON": True,
    "BORDER_RADIUS": "1rem",
    "THEME": "light",
    "COLORS": {
        "base": {
            "50": "#fcf8f2",
            "100": "#f7efe4",
            "200": "#ecdfcf",
            "300": "#dcc5ae",
            "400": "#c4a383",
            "500": "#aa805b",
            "600": "#8c6547",
            "700": "#6f4e37",
            "800": "#563d2e",
            "900": "#3f2e24",
            "950": "#261a15",
        },
        "primary": {
            "50": "#fff4eb",
            "100": "#ffe5d2",
            "200": "#ffc9a8",
            "300": "#f7a977",
            "400": "#e88747",
            "500": "#c96a2f",
            "600": "#a75324",
            "700": "#86401f",
            "800": "#6f341e",
            "900": "#5c2d1c",
            "950": "#34170e",
        },
    },
    "STYLES": [f"/{STATIC_URL}admin/css/tableos-admin.css"],
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": get_admin_sidebar_navigation,
    },
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
