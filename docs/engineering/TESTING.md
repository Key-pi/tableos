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
passed; the test suite ran **139 tests**. Ruff reported **21 pre-existing
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
