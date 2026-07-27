# Codebase inventory

This inventory is evidence-based. It combines an AST parse of all 221 tracked Python files, a review of all first-party source/test/configuration units, migration history review, and focused domain traces. Generated caches, virtual environments, and VCS internals were not treated as source.

## System footprint

| Metric | Count / evidence |
| --- | --- |
| First-party tracked files at `HEAD` | 238 |
| Python files | 221 / 20,516 LOC |
| Installed custom Django packages | `bot` plus 10 domain apps in `core/settings/base.py` |
| Persisted model classes | 31, plus shared abstract bases in `core/database/models.py` |
| Numbered migrations | 25 across the 10 domain apps |
| Direct test modules | 17 / 3,826 LOC |
| Largest code units | `bot/handlers/staff.py` (2,370 LOC), `apps/billing/services.py` (803), `apps/partners/management/commands/seed_baseline_demo.py` (728), `bot/keyboards/staff.py` (723), `apps/analytics/services.py` (718), `apps/orders/services.py` (673) |

The deployable application is a modular Django monolith with an aiogram polling process and Celery workers. There is no public REST/API layer or WebApp frontend. The only HTTP endpoints are demo home, Django admin, and healthcheck in `core/urls.py`.

## Domain app inventory

| Path | Business responsibility and persisted entities | Public entry points / consumers | Reads from / mutates / external I/O | Tenant boundary, transaction, tests, suspicions |
| --- | --- | --- | --- | --- |
| `apps/partners` | Venue, `BotInstance`, `PartnerBotSettings` | admin/forms; token rotation; demo seed commands; bot runtime/content | Own models; encryption; creates partner configuration | Partner is root scope. Settings validation is model-form based. Tests cover bot config and modules. Multiple active bots and label collisions need governance. |
| `apps/users` | Django `User`, global `TelegramAccount`, partner `GuestProfile`, admin access/RBAC | guest-profile service; admin/forms; `ensure_admin` | Global Telegram identity plus partner profiles; mutates visits/access profiles | Global identity vs partner profile is intentional. Admin scoping is in `core`. Same-partner relation validation is not durable. |
| `apps/tables` | `Table`, `TableSession`, QR/deep-link lifecycle | QR parser/activation; settlement close; admin bill actions | Reads billing/orders/bonuses/users; creates session and visit bonus | QR lookup is partner-scoped. Activation is atomic but lacks a guest/session concurrency invariant; `timed_out` has no runtime lifecycle. |
| `apps/menu` | `MenuCategory`, `MenuItem` | admin, active catalog repository, bot menu renderer | Read-only catalog queries; no real service/task implementation | All normal bot queries scope partner. `repositories.py` is a selector-shaped ORM wrapper; no direct test module. |
| `apps/orders` | `Cart`, `CartItem`, `Order`, `OrderItem`, `OrderStatusHistory` | cart/order/status services; selectors/repositories; admin actions; guest/staff handlers | Reads menu/tables/users/employees; writes orders/history/notifications | Core normal flow scopes partner. Status transition accepts a stale object and lacks row locking; generic creation API lacks full same-partner validation. |
| `apps/billing` | `Bill`, `BillingRequest`, `BillOrder`, `BillItem`, `Payment` | bill/request/payment services; staff/admin flows | Reads orders/tables/bonuses; writes money state and notifications | Service paths scope partner and use `atomic`; allocation/idempotency and admin alternate transition paths need remediation. |
| `apps/bonuses` | `BonusProgram`, `BonusTransaction`, `WalkInSale`, `WalkInSaleItem` | accrual strategies; quick-sale services; management command | Reads menu/orders/users; mutates loyalty ledger/balance | Visit/manual-purchase path exists. No production call applies `order_completed` programs. Ledger and redemption need concurrency characterization. |
| `apps/employees` | `EmployeeProfile` | staff lookup selector, admin form | Reads global Telegram identity; employee authorization is consumed by bot/notifications | Partner-scoped profile; current selector omits `User.is_active`, so disabled Django users retain staff paths. |
| `apps/notifications` | guest preferences, `StaffNotification`, `BroadcastCampaign` | delivery/broadcast services and Celery tasks; admin action | aiogram network calls; Celery/on-commit dispatch | Staff/guest messages use `on_commit`. Broadcast delivery currently performs Telegram I/O inside an atomic transaction and lacks durable batch idempotency. |
| `apps/analytics` | `PartnerDailyMetric` | daily report builders and snapshot task | Reads billing/orders/bonuses/tables/users; writes daily snapshot | Partner-scoped report queries; beat refresh is idempotent per date but full-table materialization needs scale review. |

## Bot, core, and infrastructure inventory

| Path | Responsibility | Evidence |
| --- | --- | --- |
| `bot/handlers/` | Transport/UI for start, menu, cart/order, session, profile, billing, guest calls, and staff operations | All eight routers registered by `bot/main.py`; most ORM/service calls cross `sync_to_async`. `staff.py` is a mixed 2,370-line UI/query/orchestration unit. |
| `bot/keyboards/` | Reply and inline presentation | Main/menu/profile/session/billing/guest-call/staff keyboard builders. `staff.py` has 723 LOC of presentation composition. |
| `bot/services/` | partner runtime resolution, content/capability model, navigation | Runtime maps token to partner at polling startup; `BotContent` resolves modules/buttons/templates; navigation derives browse/table state. |
| `bot/filters`, `bot/states` | partner-configured reply-label matching and quick-sale FSM | `PartnerButtonFilter` does exact label matching; collision validation is absent. `StaffQuickSaleStates` is the only FSM state group. |
| `core/` | settings, DB bases, admin scoping, Celery, crypto, logging, URL/ASGI/WSGI | PostgreSQL target + SQLite dev option; Celery queues/beat; Fernet encryption; scoped Django admin. |
| `infrastructure/` | Container build | One tracked Dockerfile. Compose is root-level and declares db/redis/web/worker; no tracked nginx/Redis/script implementation. |
| Management commands | runtime/bootstrap/manual actions | `runbot`, `ensure_admin`, `seed_demo`, `seed_baseline_demo`, `apply_manual_purchase_bonus`. `seed_demo` is non-atomic and must not silently reassign existing cross-tenant users. |

## Layer inventory and dependency direction

Every domain app has the same scaffolded filenames (`interfaces.py`, `repositories.py`, `selectors.py`, `services.py`, `tasks.py`), but their real use is uneven. Only analytics and notifications contain concrete Celery tasks. Most interfaces are unused `Protocol` placeholders; several services/tasks contain only reserved docstrings. See [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md#layer-consistency) for symbol-level decisions.

Production-oriented imports flow broadly from orchestration apps toward domain models/services:

```text
bot -> partners/users/tables/menu/orders/billing/bonuses/employees/notifications/analytics
analytics -> billing/bonuses/orders/partners/tables/users
billing -> bonuses/notifications/orders/partners/tables
orders -> employees/menu/notifications/tables/users
notifications -> employees/orders/partners/tables/users
tables -> billing/bonuses/orders/users
bonuses -> menu/orders/users
```

The AST import map found one direct module SCC: `apps.notifications.services -> apps.notifications.tasks -> apps.notifications.services`. It is currently mediated by function-local task imports and Celery registration, rather than a top-level import failure, but it is an intentional coupling to document rather than reproduce elsewhere. App-level coupling is otherwise high because reporting and billing span many aggregates; it is a modular monolith, not isolated bounded contexts.

## Schema/migration history

All numbered migrations were read as historical schema evidence. Initial migrations create the domain entities and partner FKs; later migrations add module settings, remove obsolete module flags, add one-active-cart support, and prepare carts/orders/bills for non-table flows. Current unique constraints cover public IDs, customer codes, several join rows, menu/table names, employee Telegram binding per partner, metrics, and the active-cart invariant. They do not enforce cross-FK same-partner identity or a single active table session per guest. No applied migration was edited during this audit.

## Test structure

Tests live beside apps plus `bot/tests_*.py` and `core/tests_admin.py`. They characterize many core happy paths, rendering, RBAC fieldsets, module configurations, billing, notifications, and reports. There are no focused tests for concurrent callbacks/payment/broadcasts, PostgreSQL-only constraints, post-start partner suspension, inactive `User` staff denial, tenant list-filter leakage, inline scope, or direct admin state mutation. This is a coverage gap, not a claim that existing happy-path tests lack value.
