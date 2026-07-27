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
| `apps/*/selectors.py` | scoped and unscoped reads | Useful convention but uneven tenant enforcement. | Keep read-only; require partner/actor context for public selectors where applicable. |
| `apps/*/repositories.py` | mostly ordinary ORM reads | **PREMATURE_ABSTRACTION / NAMING_PLACEMENT** candidate where no meaningful caller/boundary exists. | Prove direct/dynamic consumers before merging into selectors; no audit-phase deletion. |
| `apps/billing/interfaces.py` | reserved docstring | **PREMATURE_ABSTRACTION**. | Remove only after API/import search and test characterization in an approved cleanup batch. |
| `apps/analytics/interfaces.py`, `bonuses/interfaces.py`, `employees/interfaces.py`, `menu/interfaces.py`, `notifications/interfaces.py`, `orders/interfaces.py`, `partners/interfaces.py`, `tables/interfaces.py`, `users/interfaces.py` | protocols / interface declarations | No first-party consumers were found by repository-wide import/name search. | Treat as candidates, not confirmed dead code; retain if public/third-party contract is discovered. |
| `apps/employees/services.py`, `apps/menu/services.py` | docstrings only | **DEAD_CODE_CANDIDATE** with no implemented service behavior. | Remove/re-purpose only with package import and external API review. |
| `apps/*/tasks.py` except analytics/notifications | placeholders or absent work | **DEAD_CODE_CANDIDATE** / naming debt. | Keep no new task module unless it registers durable work. |
| `apps/analytics/tasks.py`, `apps/notifications/tasks.py` | actual Celery tasks | Valid adapters; notifications has a services/tasks import cycle. | Move scheduling boundary behind an explicit service contract, preserve task names until migration. |
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
