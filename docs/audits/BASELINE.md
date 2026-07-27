# Baseline audit — 2026-07-27

## Repository state

| Item | Observed value |
| --- | --- |
| Git root | `/Users/danil/projects/tableos` |
| Branch | `clean-project-rules` |
| HEAD | `b17ce5b4e52b0fef4db49b3171ecf516c70d22b8` (`Merge pull request #1 from Key-pi/refactor-bot-content`) |
| Pre-existing worktree changes | staged deletion `SKILLS.md`; untracked `TABLEOS_CODEBASE_AUDIT_MASTER_PROMPT.md` |
| Audit mutations | Documentation/governance artifacts only. No application Python, migrations, imports, dependencies, runtime configuration, commit, or push was changed. |

`git diff --check` and `git diff --cached --check` were clean before audit-document writes. The deleted `SKILLS.md` was not restored or used as an active instruction file.

No active `AGENTS.md`, `AGENTS.override.md`, `.codex/`, or `.agents/` directory existed at the baseline. The repository root therefore had no additional checked-in agent instruction at the start of this audit.

## Runtime and dependency management

| Area | Evidence |
| --- | --- |
| Packaging | PEP 621 in `pyproject.toml`; Hatchling build backend; no lockfile or requirements/Poetry/uv/Pipenv manifest |
| Declared Python | `>=3.10` |
| Active Python | `.venv/bin/python` → Python 3.10.20 |
| Active Django | 5.2.14 (declared `>=5.1,<6.0`) |
| Active aiogram | 3.28.2 (declared `>=3.7,<4.0`) |
| Active Celery | 5.6.3 (declared `>=5.4,<6.0`) |
| Active Ruff | 0.13.3 |
| Type checking | No mypy/pyright configuration or executable discovered |

`python3` outside `.venv` does not provide Django, so all documented Django checks must use `.venv/bin/python` (or an equivalent activated environment).

## Database modes and processes

`core/settings/env.py` selects PostgreSQL by default and SQLite only when `DJANGO_USE_SQLITE=true`. `docker-compose.yml` declares PostgreSQL 16, Redis, Django web, and a Celery worker. It does not declare a `runbot` or Celery beat service even though `core/settings/base.py` configures beat jobs and the operational documents require both processes for a full runtime.

The local environment used SQLite (`DJANGO_USE_SQLITE=true`). The existing ignored `db.sqlite3` was not modified by the checks. Tests created and destroyed their own test database.

Discovered runtime processes:

- Django HTTP/admin: `manage.py runserver`;
- Telegram polling: `manage.py runbot`;
- Celery worker, with `default` and `broadcasts` queues;
- Celery beat for scheduled campaigns and daily metrics;
- PostgreSQL and Redis in production/shared environments.

## Baseline verification

The following commands were run without installing dependencies. `PYTHONDONTWRITEBYTECODE=1` and Ruff's `--no-cache` were added only to avoid audit-created cache files.

| Command | Result |
| --- | --- |
| `env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py check` | PASS — `System check identified no issues (0 silenced).` |
| `env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py makemigrations --check --dry-run` | PASS — `No changes detected` |
| `env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py test` | PASS — 139 tests in 8.420s |
| `./.venv/bin/ruff check --no-cache .` | FAIL — 21 pre-existing findings: E501 ×11, I001 ×7, F401 ×3; 10 are auto-fixable |
| `./.venv/bin/ruff format --check --no-cache .` | FAIL — 54 files would be reformatted; 167 already formatted |
| `docker compose config --quiet` | PASS |

The failed Ruff checks were present in committed application files before this audit. Affected paths include `apps/analytics/tests.py`, `apps/billing/tests.py`, `apps/notifications/services.py`, `apps/notifications/tests.py`, `apps/orders/tests.py`, `bot/handlers/profile.py`, `bot/handlers/staff.py`, `bot/tests_menu.py`, and `bot/tests_staff.py`. They were intentionally not fixed in audit mode.

## Documentation baseline divergences

At the audit baseline, `PROJECT_CHEATSHEET.md` stated that the current baseline was “66 tests OK” and “Ruff all checks passed.” The exact current run above produced 139 passing tests and 21 Ruff findings on an unchanged application worktree. The post-baseline documentation-only correction now points to this record; the original contradiction remains recorded as `DOC_DRIFT` in [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md), not silently erased.

## Constraints on subsequent implementation

Any implementation batch must begin by preserving the pre-existing `SKILLS.md` deletion and untracked master prompt, rerunning the relevant commands above, and comparing its result with this baseline. PostgreSQL-specific behavior still needs a separate characterization/CI strategy; passing SQLite checks are not evidence of equivalent production constraints or concurrency behavior.
