# TableOS — Testing and verification

## Reproducible baseline commands

The audit executed the following commands in the repository virtual environment:

```bash
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py check
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py makemigrations --check --dry-run
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py test
./.venv/bin/ruff check --no-cache .
./.venv/bin/ruff format --check --no-cache .
docker compose config --quiet
```

At the 2026-07-27 baseline, Django check/migration check/tests/compose config
passed; the test suite ran **139 tests**. After B3.1 the suite has **199 tests**; it passes on SQLite (5
PostgreSQL-only tests skipped) and on PostgreSQL 16. Record every full run in
the table at the end of this document.

**Environment requirements.** Tests encrypt bot tokens, so
`BOT_TOKEN_ENCRYPTION_KEY` must be a valid Fernet key — put it into `.env`
(see `DEVELOPMENT.md`); without it about 60 tests error with
`BOT_TOKEN_ENCRYPTION_KEY must be configured`. The default timezone
`Europe/Kiev` must exist in the system tz database (AA-034). Ruff reported **21 pre-existing
check violations** and **54 files that would be reformatted**. Do not state
that Ruff is green until the actual baseline changes and is rerun. Full output
and tool versions are recorded in [`../audits/BASELINE.md`](../audits/BASELINE.md).

## Test selection by change type

| Change | Minimum verification |
|---|---|
| Model/migration | migration dry-run, focused tests, data/migration test; PostgreSQL if constraint or lock semantics matter |
| Tenant/permission | two-partner service + bot/admin request/form tests |
| Order/session state | state transition and duplicate callback tests; PostgreSQL concurrent test |
| Billing/bonus/money | reconciliation fixture, negative/boundary tests, PostgreSQL allocation/wallet concurrency test |
| Admin | admin change-form/action/inline/list-filter tests; assert service side effects |
| Bot routing/content | router/filter ordering, stale callback, changed label/callback schema tests |
| Celery/notifications | after-commit, retry/duplicate worker/crash behavior, task registration tests |
| Cleanup/move | import/API search, dynamic registration checks, characterization tests, `git diff --check` |

## Running the PostgreSQL-gated tests

Seven tests run only on PostgreSQL and are skipped on SQLite:
`apps/orders/test_concurrency.py`, `apps/tables/test_concurrency.py` and
`apps/billing/test_concurrency.py` (bill allocation, bonus payment, final
payment, void vs payment, cancel vs billing). SQLite also ignores `select_for_update`, so a lock that PostgreSQL
rejects (AA-033) is invisible there: **run the whole suite on PostgreSQL
before closing any batch that touches locks, constraints or queries with
`select_for_update`.**

```bash
docker compose up -d db
env DJANGO_USE_SQLITE=false POSTGRES_HOST=localhost PYTHONDONTWRITEBYTECODE=1 \
  ./.venv/bin/python manage.py test
```

The compose database user owns the database and may create the test database.
Run the whole suite the same way before a production rollout of B2/B3
migrations.

## Concurrency protocol

SQLite is not acceptable evidence for PostgreSQL row locks, partial unique
constraints, or concurrent transactions. A concurrency test should use separate
database connections/transactions, synchronize the competing operations, and
assert the durable final state (not merely that neither call threw). Required
initial cases are in AA-004 through AA-008.

## Test data safety

- Keep partner fixtures distinct and make cross-tenant assertions explicit.
- Do not use production bot tokens or user data in tests.
- Use deterministic money values and assert ledger/balance/report results.
- Preserve source-state evidence in characterization tests; annotate a test if
  it encodes a known defect pending an approved batch.

## Definition of done for a behavioral fix

1. Test demonstrates the prior defect or unsupported path.
2. Owner/service-level fix preserves authorized current behavior.
3. Relevant adapter (bot/admin/task) cannot bypass it.
4. Migration/data/rollback implications are documented.
5. Full relevant test set and documented baseline checks are run; unrelated
   pre-existing lint failures are reported separately, not hidden.

## Recorded runs

There is no CI; every run is manual. Add a row for each full run.

| Date | Commit | Database | Command scope | Result |
|---|---|---|---|---|
| 2026-07-27 | `b17ce5b` | SQLite | full suite | 139 tests OK; Ruff: 21 findings, 54 files to reformat |
| 2026-10-23 | `ddb7dce` | SQLite | full suite | 179 tests OK (2 skipped) |
| 2026-10-23 | `ddb7dce` | PostgreSQL 16 | full suite | **29 errors** — AA-033 |
| 2026-10-23 | B3.1 branch | SQLite | full suite | 199 tests OK (5 skipped); Ruff: no new findings in changed files |
| 2026-10-23 | B3.1 branch | PostgreSQL 16 | full suite | 199 tests OK |
| 2026-10-23 | B3.2 part A branch | SQLite | full suite | 227 tests OK (7 skipped) |
| 2026-10-23 | B3.2 part A branch | PostgreSQL 16 | full suite | 227 tests OK |
