# Botera Shop

Botera Shop (бывший TableOS) — multi-tenant платформа заказов и лояльности
для заведений и магазинов: Django-монолит из модулей, Telegram-бот на
aiogram, Celery, PostgreSQL и Redis. Часть сети Botera
([`docs/product/ECOSYSTEM_DIRECTION.md`](docs/product/ECOSYSTEM_DIRECTION.md)).

## Что умеет

Каждое заведение или магазин — партнёр со своим ботом. Модули включаются по
отдельности и настраиваются в Admin:

- меню;
- заказ за столом по QR со счетами;
- самовывоз;
- доставка;
- отправка почтой;
- оплата переводом заранее и/или при получении;
- бонусы и кешбэк;
- вызов персонала;
- быстрые продажи;
- отчёт за день;
- рассылки;
- источники переходов.

Статус каждой функции — в
[`docs/product/FEATURE_MATRIX.md`](docs/product/FEATURE_MATRIX.md).

## Быстрый старт в Docker

```bash
cp .env.example .env    # впишите BOT_TOKEN_ENCRYPTION_KEY, токен бота и свой Telegram ID
docker compose up --build
docker compose logs init   # логины Admin и ссылки на столы демо-заведений
```

Подробно, со списком того, что проверить, — в
[`docs/engineering/DEVELOPMENT.md`](docs/engineering/DEVELOPMENT.md#запуск-в-docker-с-нуля).
Продакшен (свой сервер, HTTPS, резервные копии) —
[`docs/engineering/DEPLOYMENT.md`](docs/engineering/DEPLOYMENT.md).

## С чего начать

- Продукт и текущий статус функций:
  [`docs/product/PRODUCT_SPEC.md`](docs/product/PRODUCT_SPEC.md) и
  [`docs/product/FEATURE_MATRIX.md`](docs/product/FEATURE_MATRIX.md)
- Как устроено сейчас:
  [`docs/architecture/AS_IS_ARCHITECTURE.md`](docs/architecture/AS_IS_ARCHITECTURE.md)
- Целевые границы и решения:
  [`docs/architecture/TARGET_ARCHITECTURE.md`](docs/architecture/TARGET_ARCHITECTURE.md)
  и [`docs/decisions/`](docs/decisions/)
- Разработка и проверки:
  [`docs/engineering/DEVELOPMENT.md`](docs/engineering/DEVELOPMENT.md) и
  [`docs/engineering/TESTING.md`](docs/engineering/TESTING.md)
- Правила репозитория для разработчиков и агентов: [`AGENTS.md`](AGENTS.md),
  для Claude — [`CLAUDE.md`](CLAUDE.md)
- Находки аудита и статус батчей:
  [`docs/audits/ARCHITECTURE_AUDIT.md`](docs/audits/ARCHITECTURE_AUDIT.md) и
  [`docs/audits/REFACTOR_ROADMAP.md`](docs/audits/REFACTOR_ROADMAP.md)
- Открытые продуктовые решения:
  [`docs/product/OPEN_PRODUCT_QUESTIONS.md`](docs/product/OPEN_PRODUCT_QUESTIONS.md)

`TABLEOS_TZ.md` и `PROJECT_CHEATSHEET.md` сохранены как исторические
документы. Их связь с текущим кодом описана в
[`docs/product/LEGACY_TZ_MIGRATION_MAP.md`](docs/product/LEGACY_TZ_MIGRATION_MAP.md).

## Проверка локально

Для быстрого знакомства можно явно включить SQLite через
`DJANGO_USE_SQLITE=true`; для поведения блокировок и ограничений как в
продакшене нужен PostgreSQL.

```bash
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py check
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py makemigrations --check --dry-run
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py test
docker compose config --quiet
```

Полная настройка, набор процессов и текущее состояние линтера описаны в
[`docs/engineering/`](docs/engineering/), а не дублируются здесь. Никогда не
кладите в документацию настоящие токены ботов, пароли и секреты окружения.
