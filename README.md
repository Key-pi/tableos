# tableos

White-label B2B platform for hospitality venues built on Django and aiogram.

## Initial architecture

- `apps/`: business domains and Django apps
- `bot/`: Telegram delivery layer, handlers, FSM, and bot runtime
- `core/`: project configuration and shared cross-cutting concerns
- `infrastructure/`: Docker, nginx, Redis, and helper scripts

## Delivery approach

1. Foundation: repository layout, settings, custom user, partner-aware base models, bot bootstrap.
2. Core domain: partners, tables, menu, orders, bonuses, and notifications.
3. Runtime flows: QR sessions, ordering, staff actions, and bot middlewares.
4. Product hardening: analytics, broadcasts, dashboards, tests, and deployment.

## Local commands

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
python manage.py runbot
celery -A core.celery worker --loglevel=info
celery -A core.celery beat --loglevel=info
python manage.py ensure_admin --username admin --email admin@tableos.local --password admin12345
python manage.py seed_demo --slug demo-lounge --name "Demo Lounge"
python manage.py seed_baseline_demo
```

## Local development

For quick local bootstrap without PostgreSQL, set `DJANGO_USE_SQLITE=true` in `.env`.
Production and shared environments should continue using PostgreSQL.

## Demo mode

Fast demo bootstrap:

```bash
python manage.py seed_demo \
  --slug demo-lounge \
  --name "Demo Lounge" \
  --with-admin \
  --with-manager
```

If you already have a Telegram bot from BotFather, add:

```bash
python manage.py seed_demo \
  --slug demo-lounge \
  --name "Demo Lounge" \
  --bot-username your_bot_username \
  --bot-token 123456:ABCDEF
```

What you get after bootstrap:

- demo partner with tables and menu items
- partner bot settings aligned with the current button-based guest flow
- optional local admin user
- optional manager user and employee profile
- printed table deep links and current guest-flow hints

For a fuller QA baseline with two venues, staff, tables, bots, menu, and loyalty rules:

```bash
python manage.py migrate
python manage.py seed_baseline_demo
```

What you get after baseline seed:

- `platform_admin / tableos12345`
- `night-owl-hookah` with 7 tables plus bar, 5 staff users, hookahs and alcohol menu
- `riverstone-cafe` with 12 tables, 8 staff users, cafe menu and alcohol
- one inactive `BotInstance` per partner with placeholder token and ready `username` for QR links
- no seeded guests or orders, so guest flows can be tested from a clean state
- current bot defaults, including `Мой профиль`, staff call, billing request, and quick-sale-ready menu data

Useful local URLs:

- `/` demo landing page
- `/admin/` Django admin
- `/health/` healthcheck

## Celery background jobs

Telegram deliveries and broadcasts are queued through Celery with Redis as broker.
Run a separate worker process in production and during local end-to-end testing:

```bash
celery -A core.celery worker --loglevel=info
```

If you want `scheduled_at` campaigns to go out automatically, run Celery Beat too:

```bash
celery -A core.celery beat --loglevel=info
```
