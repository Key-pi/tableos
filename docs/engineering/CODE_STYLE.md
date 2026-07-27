# TableOS — Code style and review rules

## Core principles

- Prefer small, explicit owner-service changes to broad rewrites.
- Preserve behavior and public imports unless an approved roadmap batch says
  exactly what changes.
- Use Django ORM idiomatically, but make scoping and transaction boundaries
  visible rather than implicit.
- Use existing project formatting/import conventions; do not combine an
  unrelated repository-wide format sweep with a behavioral fix.

## Naming and placement

| Name | Use only when |
|---|---|
| `services.py` | a module owns a command/use case, transaction, authorization or orchestration |
| `selectors.py` | a read-only scoped lookup/projection is shared or materially clarifies a query |
| `repositories.py` | a real persistence/query boundary has more value than a one-line ORM wrapper |
| `interfaces.py` | there is a documented consumer and an actual interchangeable boundary |
| `policies.py` / `rules.py` / `strategies.py` | a named, deterministic business decision is shared or independently testable |
| `tasks.py` | Celery registers durable background work for the app |

An empty file or protocol is not architecture. Before deleting or moving one,
check direct imports, public imports, Django registration, Celery autodiscovery,
aiogram router inclusion, settings strings, and management command use.

## Transaction and money rules

- Load the canonical mutable row under the appropriate lock inside the command;
  do not validate state from a stale object fetched outside it.
- State/ledger/derived total writes go through one owner service; no raw admin
  `QuerySet.update()` alternative.
- Document lock order when more than one aggregate is locked.
- Use immutable/source-linked/idempotent records for payment/bonus/delivery
  effects where repeated execution is possible.
- Network calls occur after commit and outside the database transaction.

## Review checklist

- Is `partner` derived from trusted context and checked on all related objects?
- Is actor activity/capability rechecked at action time?
- Does the change create a new unscoped selector, mutable admin field, or
  alternate lifecycle path?
- Is external I/O/retry outside `atomic`?
- Are dynamic consumers and public imports preserved?
- Is an ADR/product decision required rather than inferred?
- Are docs and focused tests updated in the same change?
