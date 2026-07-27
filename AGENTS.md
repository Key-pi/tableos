# TableOS repository instructions

## System summary

TableOS is a Django modular monolith for multi-tenant hospitality venues.
`bot` is the aiogram adapter; Django Admin and management commands are other
write/read adapters; Celery handles background delivery/analytics; PostgreSQL is
the production source of truth and Redis is the Celery broker/backend.

Read [README.md](README.md) for onboarding. The canonical governance map is:

- product: `docs/product/PRODUCT_SPEC.md`, `FEATURE_MATRIX.md`, and
  `OPEN_PRODUCT_QUESTIONS.md`;
- current architecture: `docs/architecture/AS_IS_ARCHITECTURE.md`;
- target/module/layer rules: `docs/architecture/TARGET_ARCHITECTURE.md`,
  `MODULE_BOUNDARIES.md`, `LAYERING_RULES.md`;
- security/runtime/tasks: `TENANCY_AND_PERMISSIONS.md`, `BOT_RUNTIME.md`,
  `BACKGROUND_JOBS.md` in `docs/architecture/`;
- decisions: `docs/decisions/ADR-0001.md` through `ADR-0006.md`;
- engineering: `docs/engineering/`;
- evidence/roadmap: `docs/audits/`.

`TABLEOS_TZ.md` and `PROJECT_CHEATSHEET.md` are retained legacy/evidence
documents. Do not treat them as a higher source of truth than the source code,
canonical docs, or an approved ADR.

## Mandatory workflow

1. Identify the owning module and relevant adapter boundaries before editing.
2. Search static imports and dynamic consumers (Django registration/admin,
   migrations, Celery autodiscovery, aiogram router inclusion, management
   commands, settings strings) before moving/removing code.
3. Add characterization/regression tests before changing a lifecycle, money,
   tenant, or permission behavior.
4. Make the narrowest compatible owner-service change; preserve public imports,
   Celery task names, router behavior, admin URLs, and user-facing flows unless
   an approved migration plan explicitly changes them.
5. Verify the relevant paths, inspect the diff, and update canonical docs/ADR
   when a rule or product decision changes.

## Fixed architecture rules

- `core` contains only cross-cutting technical primitives and must not import a
  domain app.
- Domain modules own their data and commands. `bot`, Django Admin, Celery tasks,
  and management commands are adapters: they call owner services/selectors and
  do not reimplement business invariants.
- `services.py` owns use cases/transactions; `selectors.py` is read-only;
  `repositories.py`, `interfaces.py`, `policies.py`, `rules.py`, and `engine.py`
  require a documented consumer and distinct responsibility.
- Do not introduce a generic event bus, repository hierarchy, microservice, or
  cache merely to make a structure look uniform.

## Tenant and permission rules

- Derive `partner` from trusted context; never trust a callback payload, UI
  visibility, submitted FK, or unscoped object as proof of tenant authority.
- Validate that actor, target, and every related object share the same partner
  in an owner service before mutation.
- A staff command requires active Django `User`, active `EmployeeProfile`,
  matching partner, and current capability at execution time. Recheck after a
  rendered card/keyboard may have gone stale.
- Scope Admin list filters, foreign keys, inlines, and actions as rigorously as
  primary querysets. Admin access is not interchangeable with staff capability.
- A suspended partner’s behavior must be decided and enforced at update/task
  time, not assumed from process startup cache.

## Django, transaction, money and async rules

- Load the canonical mutable row under the appropriate lock inside the command;
  do not transition state from a stale preloaded instance.
- State-machine, ledger, allocation, payment, derived-total, and session-close
  writes use one owner service. Do not use raw `QuerySet.update()` or editable
  Admin fields as an alternate lifecycle path.
- Document lock order and idempotency for concurrent commands. Use PostgreSQL
  tests for locks and durable constraints.
- Keep money ledgers source-linked/traceable and use explicit compensating
  policy rather than silently editing history.
- Telegram/network I/O, retry sleep, and fan-out never run inside an open
  database transaction. Persist/commit first; task delivery must be idempotent.

## Checks and definition of done

Use the exact commands in `docs/engineering/TESTING.md`. At the audited
baseline, `manage.py check`, migration dry-run, tests (139), and compose config
passed in local SQLite mode; Ruff had pre-existing failures. Do not claim a
green lint baseline without rerunning it.

A change is done only when its owner/adapter boundaries, tenant/actor checks,
transaction/lock/idempotency behavior, alternate Admin/bot/task paths, tests,
compatibility impact, and documentation/ADR updates have been reviewed.

## Prohibited without explicit owner approval

- Product semantic decisions listed in `docs/product/OPEN_PRODUCT_QUESTIONS.md`.
- Destructive data repair, migration enforcement without a data audit, or
  silent cross-tenant data cleanup.
- Broad refactors, dependency upgrades, full formatting sweeps, or module moves
  bundled into an unrelated bug fix.
- Removing a module/interface/task/repository before static, dynamic, public API
  and test evidence proves it unused.
