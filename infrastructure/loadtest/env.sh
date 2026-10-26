# Environment of the load test (source it: . infrastructure/loadtest/env.sh).
# Needs a throwaway PostgreSQL database (see docs/engineering/LOAD_TESTING.md):
#   LOADTEST_DB_PASSWORD (required), LOADTEST_DB_NAME / _USER / _HOST / _PORT.
# Secrets for this stand are generated once into .local.env next to this file.
LT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=${REPO:-$(cd "$LT_DIR/../.." && pwd)}
if [ -z "${LOADTEST_DB_PASSWORD:-}" ]; then
  echo "LOADTEST_DB_PASSWORD is not set" >&2; return 1 2>/dev/null || exit 1
fi
if [ ! -f "$LT_DIR/.local.env" ]; then
  python3 - > "$LT_DIR/.local.env" <<'PY'
import secrets
from cryptography.fernet import Fernet
print(f"export DJANGO_SECRET_KEY={secrets.token_urlsafe(50)}")
print(f"export BOT_TOKEN_ENCRYPTION_KEY={Fernet.generate_key().decode()}")
PY
fi
. "$LT_DIR/.local.env"
export PYTHONPATH=$REPO:$LT_DIR/site
export DJANGO_SETTINGS_MODULE=core.settings.production
export DJANGO_ALLOWED_HOSTS=shop.example.com
export DJANGO_USE_SQLITE=false
export POSTGRES_DB=${LOADTEST_DB_NAME:-loadtest} POSTGRES_USER=${LOADTEST_DB_USER:-loadtest}
export POSTGRES_HOST=${LOADTEST_DB_HOST:-127.0.0.1} POSTGRES_PORT=${LOADTEST_DB_PORT:-5432}
export POSTGRES_PASSWORD=$LOADTEST_DB_PASSWORD
export PGHOST=$POSTGRES_HOST PGPORT=$POSTGRES_PORT PGDATABASE=$POSTGRES_DB PGUSER=$POSTGRES_USER
export PGPASSWORD=$LOADTEST_DB_PASSWORD
export REDIS_URL=${LOADTEST_REDIS_URL:-redis://127.0.0.1:6379/0}
export CELERY_BROKER_URL=$REDIS_URL CELERY_RESULT_BACKEND=$REDIS_URL
export CELERY_TASK_ALWAYS_EAGER=false
export DJANGO_LANGUAGE_CODE=uk
export FAKE_TG=http://127.0.0.1:8081
