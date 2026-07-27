# File coverage ledger

## Method

The audit used `rg --files`, Python AST parsing, migration-operation inspection, direct source review, runtime call-chain tracing, configuration inspection, and the baseline test/check commands recorded in [BASELINE.md](BASELINE.md). `REVIEWED` means a first-party unit was included in this audit's source/AST/domain review; it does not mean every line is defect-free. `GENERATED/HISTORICAL` means read for schema/history but not evaluated against current style rules.

## Reviewed first-party source

| Coverage unit | Status | Evidence / scope |
| --- | --- | --- |
| Root: `README.md`, `PROJECT_CHEATSHEET.md`, `TABLEOS_TZ.md`, `TABLEOS_CODEBASE_AUDIT_MASTER_PROMPT.md`, `pyproject.toml`, `docker-compose.yml`, `manage.py`, `.env.example`, `.gitignore` | REVIEWED | All mandatory documents read in full; packaging, commands, environment, docs drift, and deployment topology checked. |
| `core/**/*.py` (20 files) | REVIEWED | Settings, Celery, database bases, admin mixin, crypto, logging, URLs, ASGI/WSGI, and admin tests. |
| `apps/partners/**/*.py` excluding migrations (17 files) | REVIEWED | Models, forms/admin, repositories/selectors/services, tasks/interface scaffold, tests, `seed_demo`, `seed_baseline_demo`. |
| `apps/users/**/*.py` excluding migrations (13 files) | REVIEWED | Models, forms/admin, repositories/selectors/services, command, tests, RBAC constants. |
| `apps/tables/**/*.py` excluding migrations (11 files) | REVIEWED | Models, services, repository/selector, admin, tests, scaffold. |
| `apps/menu/**/*.py` excluding migrations (10 files) | REVIEWED | Models/admin, repository/selector, scaffold; behavior additionally traced through bot/order/bonus consumers. |
| `apps/orders/**/*.py` excluding migrations (13 files) | REVIEWED | Models, services, repository/selector, admin, notification tests, state transition call chains. |
| `apps/billing/**/*.py` excluding migrations (13 files) | REVIEWED | Models, forms/admin, services, repository/selector, tests, staff/admin payment paths. |
| `apps/bonuses/**/*.py` excluding migrations (17 files) | REVIEWED | Models, strategies, services, forms/admin, repository/selector, command, tests. |
| `apps/employees/**/*.py` excluding migrations (12 files) | REVIEWED | Models/forms/admin, selector/repository, tests, staff/notification consumers. |
| `apps/notifications/**/*.py` excluding migrations (12 files) | REVIEWED | Models/admin, services/tasks, repository/selector, tests, Celery wiring. |
| `apps/analytics/**/*.py` excluding migrations (10 files) | REVIEWED | Models/admin, report/snapshot services, task, repository/selector, tests. |
| `bot/**/*.py` (40 files) | REVIEWED | Dispatcher/runtime/context/content/navigation, every handler/router, keyboards, filters, FSM, `runbot`, and six test modules. |
| `infrastructure/docker/Dockerfile` | REVIEWED | Container build only; no other tracked implementation in `infrastructure/`. |

## Schema history

| Paths | Status | Reason |
| --- | --- | --- |
| `apps/*/migrations/[0-9]*.py` (25 numbered files) | GENERATED/HISTORICAL | Parsed and reviewed for entity/constraint/lifecycle history. Not scored for generated migration formatting. |
| `apps/*/migrations/__init__.py` | GENERATED/HISTORICAL | Registration marker only. |

## Tests and checks

| Unit | Status | Evidence |
| --- | --- | --- |
| All 17 discoverable test modules | REVIEWED | AST inventory plus focused reading of tenant, state, billing, notification, bot, and report tests; full `manage.py test` passed 139 tests. |
| `manage.py check`, migration dry run, full Django test suite, Ruff check/format check, Compose config | REVIEWED | Exact outputs in [BASELINE.md](BASELINE.md). |

## Excluded with reason

| Paths / class | Status | Reason |
| --- | --- | --- |
| `.git/` | EXCLUDED_WITH_REASON | VCS internals; only status, commit, and non-destructive diffs inspected. |
| `.venv/`, `__pycache__/`, `.ruff_cache/`, `db.sqlite3`, coverage/static/media outputs | EXCLUDED_WITH_REASON | Environment/cache/generated artifacts, not source of current architecture. |
| `.idea/` (8 tracked metadata files) | EXCLUDED_WITH_REASON | IDE metadata, not runtime/application behavior. |
| `apps/background/` | EXCLUDED_WITH_REASON | Only ignored cache artifacts; no tracked source and not installed as a Django app. |
| Empty physical `infrastructure/nginx`, `infrastructure/redis`, `infrastructure/scripts` directories | EXCLUDED_WITH_REASON | No tracked files to review. |

## Needs follow-up

| Item | Status | Why |
| --- | --- | --- |
| PostgreSQL concurrency/constraint behavior | NEEDS_FOLLOW_UP | Current baseline intentionally uses SQLite; no PostgreSQL characterization or CI job exists. |
| `.agents/skills/tableos-development/` | REVIEWED / CREATED | Repository-local skill was initialized with `skill-creator`, completed, and YAML/manual structure-checked. The provided Python validator could not run because no available Python runtime has `PyYAML`; its failure is recorded in the audit summary rather than hidden. |
| Pre-existing `SKILLS.md` deletion | NEEDS_FOLLOW_UP | User-owned staged deletion; preserve unless owner explicitly decides to restore or replace it. |
| No CI configuration | NEEDS_FOLLOW_UP | No tracked workflow validates migrations, lint, PostgreSQL behavior, or bot/beat deployment. |
