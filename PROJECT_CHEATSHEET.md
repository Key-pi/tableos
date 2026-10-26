# Botera Shop: шпаргалка по проекту

> **Статус:** старая рабочая шпаргалка, механически обновлённая после аудита
> 2026-07-27 и последующих батчей. Каноническое вступление и проверенные
> команды — в [`docs/engineering/`](docs/engineering/); текущее поведение и
> известные пробелы — в
> [`docs/architecture/AS_IS_ARCHITECTURE.md`](docs/architecture/AS_IS_ARCHITECTURE.md)
> и [`docs/audits/`](docs/audits/). Сверка продукта и спецификации — в
> [`docs/product/LEGACY_TZ_MIGRATION_MAP.md`](docs/product/LEGACY_TZ_MIGRATION_MAP.md).
> Файл сохранён для исторической навигации и не важнее этих документов и кода.

## Что это

Botera Shop (бывший TableOS) — MVP из бэкенда, Admin и Telegram-бота для
заведений и магазинов:

- Django хранит доменную модель, настройки, Admin, команды демо-данных и адреса проверки здоровья и демо-страницы.
- aiogram обслуживает сценарии гостей и персонала в Telegram.
- Celery + Redis выполняют фоновую доставку: уведомления персонала, обновления статуса заказа для гостей, рассылки.
- PostgreSQL — для продакшена и общих окружений; локально можно использовать SQLite через `DJANGO_USE_SQLITE=true`.

## Главные процессы

Локально обычно нужны 3 процесса:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runserver
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runbot
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker --loglevel=info
```

Для запуска, близкого к продакшену, лучше разделять очереди:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker -Q default --loglevel=info
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker -Q broadcasts --loglevel=info
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery beat --loglevel=info
```

Проще всего — Docker (пошагово: [DEVELOPMENT.md](docs/engineering/DEVELOPMENT.md#запуск-в-docker-с-нуля)).
Docker Compose поднимает все нужные процессы — `db`, `redis`, разовый `init`, `web`,
`worker`, `beat`, `bot` (с B4). `beat` и `bot` должны работать в одном
экземпляре:

```bash
docker compose up
```

Для настоящего Telegram-бота также нужны корректный `BOT_TOKEN_ENCRYPTION_KEY` и активный `BotInstance` с настоящим токеном.

## Первичная подготовка

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py migrate
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py ensure_admin --username admin --email admin@botera.local --password "$ADMIN_PASSWORD"
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py seed_baseline_demo
```

Токены ботов и свой Telegram ID для демо-данных задаются в `.env`
(`DEMO_CAFE_BOT_TOKEN`, `DEMO_HOOKAH_BOT_TOKEN`, `DEMO_TELEGRAM_ID`), список
параметров — `manage.py seed_baseline_demo --help`.

## Полная проверка после изменений

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py check
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py test
./.venv/bin/ruff check .
```

Проверенная база на 2026-07-27 (полный вывод и версии — в [BASELINE.md](docs/audits/BASELINE.md)):

- `manage.py test`: 139 тестов OK.
- `manage.py check`: замечаний нет.
- `makemigrations --check --dry-run`: изменений нет.
- `ruff check --no-cache .`: 21 старое замечание; `ruff format --check --no-cache .`: 54 файла были бы переформатированы. Это не «зелёная» база.

После B1–B5, самовывоза, упорядочивания каналов, Docker, источников переходов, доставки, способов оплаты, отправки почтой защиты прав владельца лимита неоплаченных заказов подготовки к запуску перевода Admin «Моих заказов» возвратов, часов и уведомлений по модулю, механики языков, текстов гостя на языках, перевода персонала, уведомлений и Admin, исправления AA-040, счетов на большой истории (AA-041), повтора номеров (AA-042), бота в нескольких процессах (AA-043)
тестов стало 583, они
зелёные на SQLite и PostgreSQL 16; актуальные команды и журнал прогонов — в
[TESTING.md](docs/engineering/TESTING.md).

## Как работает Celery

Сценарий доставки:

1. Бизнес-сервис создаёт объект, например `StaffNotification`.
2. Сервис вызывает `transaction.on_commit(...)`.
3. После успешного коммита вызывается `.delay(...)` Celery.
4. Celery кладёт задачу в Redis.
5. Отдельный воркер забирает задачу.
6. Воркер отправляет сообщение через Telegram API.
7. Результат пишется обратно в модель: `delivery_status`, `delivered_at`, `delivery_error`.

Гарантии доставки (B4, [ADR-0008](docs/decisions/ADR-0008.md)): рабочие
уведомления — хотя бы один раз (временные ошибки Telegram повторяются, уже
отправленное не шлётся снова); рассылки — не больше одного раза на гостя
(строки `BroadcastDelivery`, отправка вне транзакции, зависшие отправки после
падения воркера помечаются `unknown` и не повторяются).

Ключевые файлы:

- [core/celery.py](core/celery.py)
- [apps/notifications/tasks.py](apps/notifications/tasks.py)
- [apps/notifications/services.py](apps/notifications/services.py)
- [core/settings/base.py](core/settings/base.py)

Важно: в тестах Celery работает в режиме eager, потому что `CELERY_TASK_ALWAYS_EAGER` автоматически включается для `manage.py test`. Поэтому тестам не нужен живой Redis.

## Как проверить Celery без настоящего Telegram

Проверить, что задачи зарегистрированы:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings.local'); import django; django.setup(); from core.celery import app; app.autodiscover_tasks(force=True); print(sorted(name for name in app.tasks if name.startswith('notifications.')))"
```

Ожидаемые задачи:

```text
notifications.deliver_staff_notification
notifications.process_scheduled_broadcast_campaigns
notifications.recover_stalled_broadcast_campaigns
notifications.send_broadcast_campaign
notifications.send_guest_order_status_update
```

Проверить воркер локально:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker --loglevel=info
```

Проверить воркеры по отдельным очередям:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker -Q default --loglevel=info
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker -Q broadcasts --loglevel=info
```

Если Redis не запущен, воркер будет ругаться на подключение к брокеру. Подними Redis:

```bash
docker compose -f docker-compose.yml -f infrastructure/docker/compose.ports.yml up -d redis
```

Для запуска Django без Docker база и Redis из Docker открываются на вашем
компьютере так:

```bash
docker compose -f docker-compose.yml -f infrastructure/docker/compose.ports.yml up -d db redis
```

## Как устроены основные сценарии

Гость за столом:

1. Сканирует QR стола — это deep link на бота.
2. `/start table_<номер>_<токен>` активирует `TableSession` в визите стола (подсесть к компании или начать новый визит).
3. Смотрит меню, добавляет позиции в `Cart`.
4. Оформление создаёт `Order` и `OrderItem`.
5. Персонал получает уведомление через Celery.
6. Гость может открыть «Мой стол», подтвердить получение, запросить счёт или позвать персонал.

Гость на самовывозе ([PICKUP.md](docs/architecture/PICKUP.md)):

1. Нажимает «Самовывоз» — корзина переходит в этот способ заказа.
2. Выбирает время, делится телефоном, решает, списать ли бонусы.
3. Получает реквизиты для перевода; персонал подтверждает оплату и принимает заказ.

Персонал:

1. У сотрудника должен быть `EmployeeProfile` с привязанным `TelegramAccount`.
2. `/staff` открывает главный экран персонала.
3. Персонал видит открытые заказы, уведомления, столы, счета, переводы самовывоза.
4. Статус заказа меняется через `transition_order_status`.
5. Оплаты идут через сервисы счетов и `Payment`.
6. Быстрая продажа идёт через FSM `StaffQuickSaleStates` и `WalkInSale`.

Счета:

1. Заказы группируются в `Bill` (за столом — в пределах визита).
2. `BillItem` фиксирует позиции внутри счёта; изменения — в `BillAdjustment`.
3. `Payment` фиксирует оплату.
4. При полной оплате сервис синхронизирует `Order.paid_at`, статус и может закрыть `TableSession`.
5. Кассы, фискализация и онлайн-оплата пока только заготовки в моделях.

## Где что искать

- Настройки: [core/settings](core/settings/base.py)
- URL: [core/urls.py](core/urls.py)
- Ограничение Admin по партнёру и права: [core/admin_mixins.py](core/admin_mixins.py)
- Celery: [core/celery.py](core/celery.py)
- Партнёры, боты, часы работы: [apps/partners](apps/partners/models.py)
- Пользователи, гости, доступ к Admin: [apps/users](apps/users/models.py)
- Столы, QR-сессии, визиты: [apps/tables](apps/tables/services.py)
- Меню: [apps/menu](apps/menu/models.py)
- Заказы и корзина: [apps/orders](apps/orders/services.py)
- Счета: [apps/billing](apps/billing/services.py); стол — [table_bills.py](apps/billing/table_bills.py); самовывоз — [remote_orders.py](apps/billing/remote_orders.py)
- Бонусы и быстрая продажа: [apps/bonuses](apps/bonuses/services.py)
- Уведомления: [apps/notifications](apps/notifications/services.py)
- Аналитика и отчёт дня: [apps/analytics](apps/analytics/services.py)
- Обработчики бота: [bot/handlers](bot/handlers/staff.py)

## Правила разработки

Полные правила — в [AGENTS.md](AGENTS.md) и [CLAUDE.md](CLAUDE.md). Кратко:

- Бизнес-логику держать в `services.py`.
- Обработчики бота и Admin вызывают сервисы, а не повторяют бизнес-правила.
- Всё, что принадлежит партнёру, имеет `partner_id` и фильтруется по партнёру.
- В изменяющих сценариях использовать `transaction.atomic`.
- Внешние вызовы не делать внутри транзакции: задача Celery через `transaction.on_commit`.
- Массовые рассылки держать в отдельной очереди Celery, чтобы они не мешали уведомлениям о заказах.
- В обработчиках aiogram оборачивать вызовы Django ORM и сервисов в `sync_to_async`.
- После изменения моделей всегда запускать `makemigrations --check --dry-run`.

## Частые проблемы

`ModuleNotFoundError: celery`

```bash
./.venv/bin/pip install -e .
```

Воркер не подключается к Redis:

```bash
docker compose -f docker-compose.yml -f infrastructure/docker/compose.ports.yml up -d redis
```

Бот не стартует:

- Проверь `BOT_TOKEN_ENCRYPTION_KEY`.
- Проверь, что `BotInstance` активный.
- Проверь, что у партнёра `status=active`.
- `runbot` поддерживает только режим polling.
- Шаблонный токен из демо-данных не считается корректным.

Уведомления не приходят:

- Запущен ли воркер Celery?
- Запущен ли Redis?
- Есть ли у партнёра активный `BotInstance` в режиме polling с настоящим токеном?
- Есть ли у сотрудника `EmployeeProfile.telegram_account`?
- Включены ли `bot_notifications_enabled` и нужный флаг уведомлений?
- Смотри `StaffNotification.delivery_status` и `delivery_error` в Admin.

Admin не показывает данные партнёра:

- Проверь `AdminAccessProfile`.
- Проверь `can_access_admin`, `is_active`.
- Проверь `AdminSectionPermission`.
- Суперпользователь видит всё, администратор арендатора — только свой `partner`.

## Что ещё не сделано

- Нет публичного REST/API-слоя.
- Нет Telegram WebApp и фронтенда.
- Режим webhook для Telegram не реализован.
- Сгорание бонусов, синхронизация с кассой и фискализацией не вынесены в полноценные фоновые интеграции.
- Договоры с провайдерами касс, фискализации и онлайн-оплаты не реализованы.
- Доставка и отправка почтой — следующие способы заказа после самовывоза.
