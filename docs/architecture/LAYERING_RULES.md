# TableOS — Layering rules

**Status:** target conventions.  The table also records the audited current
placement so future changes can consolidate deliberately instead of applying a
mechanical rename.

## Contract for each layer

| Layer | Permitted responsibility | Must not do |
|---|---|---|
| Model / migration | persistence shape, local validation, durable constraints | orchestrate Telegram, perform cross-module workflow, hide transactions in `save()` |
| Application service | command/use-case orchestration, authorization context, transaction/lock boundary, invariant enforcement | render bot UI or silently accept unscoped foreign objects |
| Selector | read-only, scoped queries and projections | mutate, send notifications, determine a state transition |
| Repository | a reusable persistence boundary that is more than ORM aliases | become a second generic query API with no callers |
| Policy | a named, stable authorization/capability decision | own database persistence or external I/O |
| Rule / strategy | deterministic business calculation/eligibility | fetch arbitrary global state or mutate a foreign aggregate |
| Engine | coordinate a genuinely complex algorithm with multiple rules | replace an ordinary owner service |
| Task | deserialize durable work and invoke an idempotent service | hold a DB transaction across network I/O |
| Bot/admin adapter | authenticate intent, validate UI payload, call service, render result | write state directly or duplicate domain rules |

## Current placement audit

| Area / files | Current role | Assessment | Safe target action |
|---|---|---|---|
| `apps/*/models.py` | Django models, choices, some `clean()` | Keep as aggregate data/local constraints; cross-tenant relations remain underconstrained. | Add only evidence-backed durable constraints and service guards in approved batches. |
| `apps/orders/services.py`, `billing/services.py`, `tables/services.py`, `bonuses/services.py`, `notifications/services.py` | Primary use-case logic | Correct direction, but transaction/locking and owner boundaries are inconsistent. | Preserve public functions; characterize then harden each lifecycle seam. |
| `apps/*/selectors.py` (employees, orders, bonuses, users) | read-only lookups with consumers | Every remaining selector has a caller; the unscoped ones were removed in B5 and `get_orders_for_session` requires `partner_id`. | Keep read-only and partner-scoped. |
| `apps/*/repositories.py` (billing, bonuses, menu, notifications, orders) | query helpers with consumers | Zero-consumer repositories were removed in B5. | Add only with a real caller. |
| `interfaces.py`, placeholder `tasks.py`, docstring-only `services.py` | — | Removed in B5 (no static or dynamic consumers; Celery registrations unchanged). | Recreate only with a documented consumer (ADR-0005). |
| `apps/analytics/tasks.py`, `apps/notifications/tasks.py` | actual Celery tasks | Valid adapters. `notifications.services` reaches `tasks` only through function-local imports at enqueue time — accepted (B5): breaking it would move fan-out control into tasks for no runtime gain. | Preserve task names. |
| `apps/bonuses/strategies.py` | bonus rule implementations | This is the repository's effective rule layer. | Name/document as rules or retain strategy naming consistently; make source/idempotency explicit. |
| `policies.py`, `rules.py`, `engine.py` | no production modules found | Absence is not a defect. | Add only for a demonstrated policy/rule/algorithm seam. |
| `bot/handlers`, keyboards, filters | adapter/presentation layer | Handles significant workflow branching and content matching. | Move invariant decisions into owner services; leave Telegram presentation here. |
| `apps/*/admin.py`, forms | second UI/write boundary | Scoped reads are useful; direct lifecycle writes bypass services. | Restrict to readonly/protected fields and service-backed actions. |

## Evidence standard before removal or relocation

A candidate is eligible for an approved cleanup only after all of the following
are recorded in the change:

1. static import/name search across first-party code;
2. dynamic registration search (Django apps/admin, Celery autodiscovery,
   aiogram router inclusion, signals, settings/import strings);
3. public import/API compatibility check;
4. focused characterization test or an explicit reason it is unnecessary; and
5. a narrow rollback path.

The audit found no evidence sufficient to delete a module today.  This is why
the roadmap treats cleanup as a later, separately approved batch.
