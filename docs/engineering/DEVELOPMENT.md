# TableOS — Development guide

**Status:** source-verified onboarding. For exact baseline outcomes see
[`../audits/BASELINE.md`](../audits/BASELINE.md).

## Prerequisites

- Python 3.10 or later (the audited virtual environment used Python 3.10.20).
- PostgreSQL for production-like data, migrations, constraints, and concurrency
  verification.
- Redis plus a Celery worker for asynchronous delivery.
- A valid encrypted Telegram bot configuration for real polling; placeholder
  seed tokens are not a working bot credential.

The project declares a PEP 621/Hatchling package in `pyproject.toml`; no lock
file was present in the audited tree. Use the project virtual environment or an
equivalent isolated environment, and do not record personal/production secrets
in documentation, command history, or fixtures.

## Local setup

SQLite is allowed for quick local exploration only:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py migrate
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runserver
```

For behavior involving `select_for_update`, partial constraints, or concurrent
workers, configure PostgreSQL instead. SQLite success is not evidence that a
production locking invariant works.

Useful local processes are separate:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runserver
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runbot
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker --loglevel=info
```

If scheduled broadcast processing is part of the environment, explicitly run
and document the scheduler/beat process as well. The supplied compose file does
not alone prove that bot polling and beat are deployed.

## Seeds and local data

Management commands can create demo/baseline data. Treat all generated login
details as local-only output, not stable credentials to copy into docs. Seed
behavior is covered by AA-019: do not use it as an implicit production data
migration, and investigate a partial failure before rerunning it against shared
data.

## Change workflow

1. Read root [`AGENTS.md`](../../AGENTS.md) and the relevant module/architecture
   document before editing.
2. Search static imports and dynamic registrations (Django Admin, migrations,
   Celery, aiogram routers, management commands) before moving/removing code.
3. Add or update a focused characterization/regression test before changing an
   invariant or documented behavior.
4. Keep tenant scope, actor authorization, transaction/lock order, and external
   side effects explicit in the owner service.
5. Run the relevant checks below, then update canonical docs/ADR when a rule or
   decision changes.

## Required implementation boundaries

- Bot handlers, admin actions, and Celery tasks are adapters: call owner
  services/selectors; do not directly mutate operational/money state.
- Do not perform network I/O, retry sleep, or fan-out within `transaction.atomic`.
- Do not rely on callback payload, UI visibility, or list filtering as proof of
  tenant authorization.
- Preserve public imports and task names unless an approved compatibility plan
  says otherwise.
- Do not add a repository/interface/policy/engine merely to match a folder
  pattern; use the layer rules in
  [`../architecture/LAYERING_RULES.md`](../architecture/LAYERING_RULES.md).
