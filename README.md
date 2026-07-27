# TableOS

TableOS is a multi-tenant hospitality platform built as a Django modular
monolith with an aiogram Telegram bot, Celery, PostgreSQL, and Redis.

## Start here

- Product contract and current feature status:
  [`docs/product/PRODUCT_SPEC.md`](docs/product/PRODUCT_SPEC.md) and
  [`docs/product/FEATURE_MATRIX.md`](docs/product/FEATURE_MATRIX.md)
- Current implementation:
  [`docs/architecture/AS_IS_ARCHITECTURE.md`](docs/architecture/AS_IS_ARCHITECTURE.md)
- Target boundaries and decisions:
  [`docs/architecture/TARGET_ARCHITECTURE.md`](docs/architecture/TARGET_ARCHITECTURE.md)
  and [`docs/decisions/`](docs/decisions/)
- Development and checks:
  [`docs/engineering/DEVELOPMENT.md`](docs/engineering/DEVELOPMENT.md) and
  [`docs/engineering/TESTING.md`](docs/engineering/TESTING.md)
- Repository rules for contributors and agents: [`AGENTS.md`](AGENTS.md)
- Audited baseline, findings, and proposed batches:
  [`docs/audits/`](docs/audits/)

`TABLEOS_TZ.md` and `PROJECT_CHEATSHEET.md` are retained as legacy/evidence
documents. Their relationship to current code is mapped in
[`docs/product/LEGACY_TZ_MIGRATION_MAP.md`](docs/product/LEGACY_TZ_MIGRATION_MAP.md).

## Local verification

For quick local exploration, SQLite is available explicitly via
`DJANGO_USE_SQLITE=true`; PostgreSQL is required for production-like locking and
constraint behavior.

```bash
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py check
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py makemigrations --check --dry-run
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py test
docker compose config --quiet
```

The complete, source-verified setup, process topology, and current lint baseline
are documented in [`docs/engineering/`](docs/engineering/), rather than copied
here. Never put real bot tokens, passwords, or environment secrets into this
repository’s documentation.
