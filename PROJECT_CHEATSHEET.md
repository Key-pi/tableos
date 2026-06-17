# TableOS: шпаргалка по проекту

## Что это

TableOS сейчас работает как backend/admin/Telegram-bot MVP для заведений:

- Django хранит доменную модель, настройки, admin, seed-команды и health/demo endpoints.
- aiogram обслуживает гостевой и staff Telegram flows.
- Celery + Redis обрабатывают фоновые доставки: staff notifications, guest order status updates, broadcasts.
- PostgreSQL предполагается для prod/shared окружений; SQLite можно использовать локально через `DJANGO_USE_SQLITE=true`.

## Главные процессы

Локально обычно нужны 3 процесса:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runserver
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runbot
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker --loglevel=info
```

Для production-like запуска лучше разделять очереди:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker -Q default --loglevel=info
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker -Q broadcasts --loglevel=info
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery beat --loglevel=info
```

Если запускаешь через Docker Compose:

```bash
docker compose up db redis web worker
```

Для реального Telegram-бота также нужен валидный `BOT_TOKEN_ENCRYPTION_KEY` и активный `BotInstance` с настоящим токеном.

## Первичная подготовка

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py migrate
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py ensure_admin --username admin --email admin@tableos.local --password admin12345
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py seed_baseline_demo
```

Быстрый seed одного демо-заведения:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py seed_demo --slug demo-lounge --name "Demo Lounge" --with-admin --with-manager
```

## Полная проверка после изменений

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py check
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py test
./.venv/bin/ruff check .
```

Ожидаемый текущий baseline:

- `manage.py test`: 66 tests OK.
- `manage.py check`: no issues.
- `makemigrations --check --dry-run`: no changes detected.
- `ruff check .`: all checks passed.

## Как работает Celery

Сценарий доставки:

1. Бизнес-сервис создаёт объект, например `StaffNotification`.
2. Сервис вызывает `transaction.on_commit(...)`.
3. После успешного commit вызывается Celery `.delay(...)`.
4. Celery кладёт задачу в Redis.
5. Отдельный worker забирает задачу.
6. Worker отправляет сообщение через Telegram API.
7. Результат пишется обратно в модель: `delivery_status`, `delivered_at`, `delivery_error`.

Ключевые файлы:

- [core/celery.py](/Users/danil/projects/tableos/core/celery.py)
- [apps/notifications/tasks.py](/Users/danil/projects/tableos/apps/notifications/tasks.py)
- [apps/notifications/services.py](/Users/danil/projects/tableos/apps/notifications/services.py)
- [core/settings/base.py](/Users/danil/projects/tableos/core/settings/base.py)

Важно: в тестах Celery работает в eager-режиме, потому что `CELERY_TASK_ALWAYS_EAGER` автоматически включается для `manage.py test`. Поэтому тесты не требуют живого Redis.

## Как проверить Celery без реального Telegram

Проверить, что задачи зарегистрированы:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.local'); import django; django.setup(); from core.celery import app; app.autodiscover_tasks(force=True); print(sorted(name for name in app.tasks if name.startswith('notifications.')))"
```

Ожидаемые задачи:

```text
notifications.deliver_staff_notification
notifications.send_broadcast_campaign
notifications.send_guest_order_status_update
```

Проверить worker локально:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker --loglevel=info
```

Проверить worker'ы по отдельным очередям:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker -Q default --loglevel=info
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker -Q broadcasts --loglevel=info
```

Если Redis не запущен, worker будет ругаться на broker connection. Подними Redis:

```bash
docker compose up redis
```

## Как устроены основные flows

Гость:

1. Открывает Telegram bot через QR deep link стола.
2. `/start table_<number>_<token>` активирует `TableSession`.
3. Гость смотрит меню, добавляет позиции в `Cart`.
4. Checkout создаёт `Order` и `OrderItem`.
5. Staff получает уведомление через Celery.
6. Гость может смотреть `Мой стол`, подтвердить получение, запросить счёт или позвать персонал.

Персонал:

1. Сотрудник должен иметь `EmployeeProfile` с привязанным `TelegramAccount`.
2. `/staff` открывает staff home.
3. Staff видит открытые заказы, уведомления, столы, счета.
4. Статус заказа меняется через `transition_order_status`.
5. Оплаты идут через billing services и `Payment`.
6. Быстрая продажа идёт через FSM `StaffQuickSaleStates` и `WalkInSale`.

Биллинг:

1. Заказы группируются в `Bill`.
2. `BillItem` фиксирует позиции внутри счёта.
3. `Payment` фиксирует оплату.
4. При полной оплате сервис синхронизирует `Order.paid_at`, статус и может закрыть `TableSession`.
5. POS/fiscal/online payment интеграции пока только модельный задел.

## Где что искать

- Настройки: [core/settings](/Users/danil/projects/tableos/core/settings/base.py)
- URL: [core/urls.py](/Users/danil/projects/tableos/core/urls.py)
- Admin scoping/RBAC: [core/admin_mixins.py](/Users/danil/projects/tableos/core/admin_mixins.py)
- Celery: [core/celery.py](/Users/danil/projects/tableos/core/celery.py)
- Партнёры и боты: [apps/partners](/Users/danil/projects/tableos/apps/partners/models.py)
- Пользователи/гости/admin access: [apps/users](/Users/danil/projects/tableos/apps/users/models.py)
- Столы/QR-сессии: [apps/tables](/Users/danil/projects/tableos/apps/tables/services.py)
- Меню: [apps/menu](/Users/danil/projects/tableos/apps/menu/models.py)
- Заказы/корзина: [apps/orders](/Users/danil/projects/tableos/apps/orders/services.py)
- Биллинг: [apps/billing](/Users/danil/projects/tableos/apps/billing/services.py)
- Бонусы/quick sale: [apps/bonuses](/Users/danil/projects/tableos/apps/bonuses/services.py)
- Уведомления: [apps/notifications](/Users/danil/projects/tableos/apps/notifications/services.py)
- Аналитика/отчёт дня: [apps/analytics](/Users/danil/projects/tableos/apps/analytics/services.py)
- Bot handlers: [bot/handlers](/Users/danil/projects/tableos/bot/handlers/staff.py)

## Правила разработки

- Бизнес-логику держать в `services.py`.
- Bot/admin handlers должны вызывать сервисы, а не повторять бизнес-правила.
- Всё tenant-scoped должно иметь `partner_id` и фильтроваться по партнёру.
- В мутирующих сценариях использовать `transaction.atomic`.
- Внешние вызовы не делать внутри транзакции напрямую: Celery task через `transaction.on_commit`.
- Массовые broadcast-задачи держать в отдельной Celery queue, чтобы они не мешали staff/order notifications.
- Для aiogram handlers использовать `sync_to_async` вокруг Django ORM/service calls.
- После изменения моделей всегда запускать `makemigrations --check --dry-run`.

## Частые проблемы

`ModuleNotFoundError: celery`

```bash
./.venv/bin/pip install -e .
```

Worker не подключается к Redis:

```bash
docker compose up redis
```

Bot не стартует:

- Проверь `BOT_TOKEN_ENCRYPTION_KEY`.
- Проверь, что `BotInstance` активный.
- Проверь, что partner имеет `status=active`.
- `runbot` поддерживает только polling mode.
- Placeholder seed token не считается валидным.

Уведомления не приходят:

- Запущен ли Celery worker?
- Запущен ли Redis?
- Есть ли у партнёра активный polling `BotInstance` с настоящим токеном?
- У сотрудника есть `EmployeeProfile.telegram_account`?
- Включены ли `bot_notifications_enabled` и нужный флаг уведомлений?
- Смотри `StaffNotification.delivery_status` и `delivery_error` в admin.

Admin не показывает данные партнёра:

- Проверь `AdminAccessProfile`.
- Проверь `can_access_admin`, `is_active`.
- Проверь `AdminSectionPermission`.
- Superuser видит всё, tenant admin видит только свой `partner`.

## Что ещё не закрыто

- Нет публичного REST/API слоя.
- Нет Telegram WebApp/frontend.
- Webhook mode для Telegram пока не реализован.
- Bonus expiration, POS sync и fiscal sync ещё не вынесены в полноценные фоновые интеграции.
- POS/fiscal/online payment provider contracts пока не реализованы.



docker run --name tableos-redis -p 6379:6379 -d redis:7-alpine - для поднятия контейнера редиса отдельно при локал тесете
