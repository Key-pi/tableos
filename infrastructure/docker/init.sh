#!/bin/sh
# One-shot start-up job of docker-compose (service `init`): check the settings,
# apply migrations and load demo data. Every other app service starts only
# after it has finished successfully.
set -e

python - <<'PY'
import sys

from cryptography.fernet import Fernet

from core.settings.env import app_settings  # reads the environment and .env

key = app_settings.bot_token_encryption_key.strip()
try:
    Fernet(key.encode())
except Exception:
    sys.exit(
        "\nBOT_TOKEN_ENCRYPTION_KEY в .env пустой или некорректный.\n"
        "Сгенерируйте ключ и впишите его в .env:\n"
        "  openssl rand -base64 32 | tr '+/' '-_'\n"
        "Ключ задаётся один раз: со сменой ключа сохранённые токены ботов\n"
        "перестанут расшифровываться.\n"
    )
PY

echo "==> Миграции"
python manage.py migrate --noinput

case "${DEMO_SEED:-true}" in
  false|0|no)
    echo "==> Демо-данные выключены (DEMO_SEED=${DEMO_SEED})"
    ;;
  *)
    echo "==> Демо-данные"
    python manage.py seed_baseline_demo --keep-existing
    ;;
esac
