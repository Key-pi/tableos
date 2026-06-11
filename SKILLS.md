---
name: tableos-project
description: Use when working on TableOS, a Django and aiogram multi-tenant Telegram platform for hospitality venues in /Users/danil/projects/tableos. Covers local commands, architecture, domain boundaries, verification, and known unfinished areas.
---

# TableOS Project Skill

## Project Shape

TableOS is a white-label B2B platform for hospitality venues. The current product is backend/admin/bot-first: Django powers domain models, admin, services, and demo routes; aiogram powers guest and staff Telegram flows.

Main folders:

- `apps/`: Django domain apps.
- `bot/`: aiogram delivery layer, handlers, keyboards, FSM, runtime helpers.
- `core/`: settings, URLs, shared admin mixins, database base models, crypto.
- `infrastructure/`: currently a minimal Dockerfile plus empty future infra folders.

Do not assume this workspace is a Git repository. In this checkout, `git status` fails because `.git` is absent.

## Local Commands

Use the project virtualenv, not system Python:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py check
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py test
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py makemigrations --check --dry-run
./.venv/bin/ruff check .
```

Useful app commands:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py migrate
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py ensure_admin --username admin --email admin@tableos.local --password admin12345
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py seed_demo --slug demo-lounge --name "Demo Lounge" --with-admin --with-manager
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py seed_baseline_demo
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runserver
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runbot
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker --loglevel=info
```

`BOT_TOKEN_ENCRYPTION_KEY` must be a valid Fernet key for real bot token work. Use `.env.example` for variable names; avoid reading or exposing `.env`.

## Architecture Rules

Keep business rules in `services.py`. Models are mostly data and small invariants; admin and bot handlers should call services instead of duplicating transitions or totals.

Use `repositories.py` and `selectors.py` for reusable querysets, especially when aiogram handlers need `select_related`/`prefetch_related` to avoid async lazy-loading surprises.

Keep all tenant data partner-scoped. Most domain models inherit `PartnerBoundModel`; admin surfaces should use `ScopedAdminMixin` and the right `AdminSection`.

For mutating flows, prefer `transaction.atomic`, `select_for_update`, explicit status transitions, and append-only history records where they already exist.

Do not call Telegram, POS, payment, fiscal, or analytics-heavy work directly from request/admin/bot mutations. Enqueue Celery tasks and process them through a separate `celery -A core.celery worker --loglevel=info` process.

In bot handlers, resolve the partner from the active bot token via `resolve_partner_for_bot_token`, then call sync Django code through `sync_to_async`.

Admin permissions and staff bot role checks are separate systems today. Do not assume changing `AdminAccessProfile` updates staff-bot capabilities.

## Domain Map

Partners:

- `Partner`: tenant/venue.
- `BotInstance`: encrypted Telegram token, username, mode. `runbot` supports active polling bots only.
- `PartnerBotSettings`: labels, feature toggles, templates, and small extra config.

Users and access:

- `User`: custom Django user with partner and role.
- `TelegramAccount`: global Telegram identity.
- `GuestProfile`: partner-specific guest, customer code, loyalty balance.
- `AdminAccessProfile` and `AdminSectionPermission`: tenant admin access.

Guest operations:

- `Table` and `TableSession`: QR deep links and active visit sessions.
- `MenuCategory` and `MenuItem`: partner menu.
- `Cart`, `CartItem`, `Order`, `OrderItem`, `OrderStatusHistory`: cart, checkout, order workflow, assignment, paid/received finality.

Money and loyalty:

- `Bill`, `BillItem`, `BillOrder`, `Payment`, `FiscalReceipt`, `BillingRequest`: shared/personal bills, partial/full payments, draft issue, manual fiscal record shell.
- `BonusProgram`, `BonusTransaction`, `WalkInSale`, `WalkInSaleItem`: cashback/visit/milestone accrual, redemption via bill, quick cashier sale.

Operations:

- `EmployeeProfile`: staff identity, notification preferences, rates.
- `StaffNotification`, `NotificationPreference`, `BroadcastCampaign`: staff delivery, guest marketing preferences, broadcasts.
- `PartnerDailyMetric`: persisted metric model; most reporting is currently generated on demand in `apps/analytics/services.py`.

## Current Working Surface

Implemented and verified locally:

- Django system check passes.
- `ruff check .` passes.
- `manage.py test` passes: 66 tests.
- Migrations are current: `makemigrations --check --dry-run` reports no changes.

Ready-ish product areas:

- Tenant-aware Django admin with scoped access and partner-visible field filtering.
- Demo home page, `/admin/`, `/health/`.
- QR table activation and guest profile/customer code creation.
- Telegram guest flow: menu browsing, cart, checkout, session view, receive confirmation, loyalty profile, staff call, bill request.
- Telegram staff flow: open orders, notifications, order status changes, table ops, bills/payments, bonus redemption, quick sale, daily report.
- Billing service: shared/personal bills, partial payments, full-payment order sync, table-session closing when settled.
- Bonus service: visit/order/manual purchase accrual, redemption cap, quick sale from menu items.
- Celery worker: Redis-backed background delivery through `core.celery` and app `tasks.py`.
- Notification service: staff messages and broadcasts are queued through Celery, then delivered by worker with delivery status.
- Demo seed commands for one venue or a fuller two-venue baseline.

## Known Unfinished Areas

There is no public API layer and no real frontend/WebApp. `core/urls.py` exposes only demo home, admin, and healthcheck.

Telegram runtime is polling-only. `BotInstance.Mode.WEBHOOK` exists, but forms and runtime reject active webhook bots.

Background work now exists through Celery + Redis, but most non-notification `tasks.py` files are still placeholders. Bonus expiration, scheduled campaigns, POS sync, and analytics snapshots still need task handlers.

POS, fiscal, and online payment integrations are placeholders. Data models exist, but provider contracts and sync jobs are not built.

Bonus expiration and scheduled campaign processing are not implemented. `CUSTOM_BONUS_STRATEGIES` is an empty registry for future partner-specific rules.

Infrastructure is minimal: Dockerfile and docker-compose exist, but nginx/redis/scripts folders have no implementation.

Reporting is mostly on-demand service logic; `PartnerDailyMetric` exists but no scheduled aggregation pipeline is present.

The local workspace contains ignored artifacts such as `.venv`, `.env`, `db.sqlite3`, `__pycache__`, `.ruff_cache`, and `.idea`. Leave them alone unless the user explicitly asks.
