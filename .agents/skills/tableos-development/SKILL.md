---
name: tableos-development
description: Implement focused TableOS changes safely in the Django, aiogram, Celery modular monolith. Use for application code, models/migrations, bot handlers, Admin, tasks, tests, configuration, or documentation changes that affect tenant scope, permissions, money, lifecycle, background work, or public imports.
---

# TableOS Development

## Overview

Use this skill to make a narrow, evidence-backed TableOS change without bypassing the repository's tenant, money, lifecycle, async, and documentation governance. It is not a license for a broad cleanup or architecture rewrite.

## Required reading

Before editing, read root `AGENTS.md` and the applicable canonical documents:

- `docs/architecture/MODULE_BOUNDARIES.md` and `LAYERING_RULES.md` for owner and placement;
- `docs/architecture/TENANCY_AND_PERMISSIONS.md` for actor/tenant work;
- `docs/architecture/BOT_RUNTIME.md` for aiogram routing/callback work;
- `docs/architecture/BACKGROUND_JOBS.md` for Celery/external-delivery work;
- `docs/architecture/TARGET_ARCHITECTURE.md` and relevant ADRs for design choices;
- `docs/engineering/TESTING.md` for verification; and
- `docs/product/OPEN_PRODUCT_QUESTIONS.md` before choosing a product semantic.

If the request depends on an open product decision, stop after safe characterization work and ask the owner; do not infer the policy.

## Focused change workflow

1. Locate the owning module and all entry adapters (bot, Admin, task, command, API) with `rg`; check static imports and dynamic registrations before moving or deleting anything.
2. State the invariant, trusted actor/partner context, transaction boundary, lock order, idempotency behavior, and external side effects before coding.
3. Add a focused characterization or regression test first. Use PostgreSQL for lock, partial-constraint, or concurrent-worker behavior.
4. Make the smallest compatible owner-service change. Keep handlers, Admin, and tasks as adapters; preserve documented public imports/task names unless a migration plan explicitly changes them.
5. Exercise every alternate write boundary. A safe service fix is incomplete if Django Admin, Celery, or a bot callback can still bypass it.
6. Run the relevant checks, inspect the diff, and update canonical docs/ADR when an invariant or decision changes.

## Non-negotiable implementation rules

- Derive `partner` from trusted context and validate all related objects share it. Never trust callback payloads, UI visibility, or a submitted FK alone.
- Require active Django user, active employee profile, matching partner, and current capability for staff commands.
- Reload the canonical mutable row under the right lock inside the transaction; do not transition from a stale object fetched earlier.
- Do not mutate money/state/ledger/derived totals through raw `QuerySet.update`, direct Admin fields, or a duplicate adapter-specific implementation.
- Do not call Telegram/network I/O or retry/sleep inside `transaction.atomic`. Persist/commit first, then schedule or perform idempotent work.
- Do not add an interface/repository/policy/rule/engine without a real consumer and a distinct responsibility.
- Do not perform a broad formatting sweep, dependency upgrade, or module move while fixing an unrelated behavior.

## Verification baseline

Use the project virtual environment and the commands in `docs/engineering/TESTING.md`. The audited baseline has 139 passing Django tests in SQLite mode, while Ruff currently has pre-existing failures; report those separately rather than masking them. Run `manage.py makemigrations --check --dry-run` for model changes and `docker compose config --quiet` for deployment configuration changes. Add PostgreSQL verification where required by the change.

## Completion checklist

- [ ] Owner module and dynamic consumers identified.
- [ ] Tenant/actor and lifecycle authority checked at every adapter.
- [ ] Transaction, lock, idempotency, and external I/O boundary explicit.
- [ ] Focused tests pass in the correct database/runtime environment.
- [ ] Public compatibility and rollback considered.
- [ ] Canonical docs/ADR updated where the rule changed.
