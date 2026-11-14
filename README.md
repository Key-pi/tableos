# Botera Shop

[![Тесты](https://github.com/Key-pi/tableos/actions/workflows/tests.yml/badge.svg?branch=clean-project-rules)](https://github.com/Key-pi/tableos/actions/workflows/tests.yml)

Botera Shop (бывший TableOS) — multi-tenant платформа заказов и лояльности
для заведений и магазинов: Django-монолит из модулей, Telegram-бот на
aiogram, Celery, PostgreSQL и Redis. Часть сети Botera
([`docs/product/ECOSYSTEM_DIRECTION.md`](docs/product/ECOSYSTEM_DIRECTION.md)).

## Что умеет

Каждое заведение или магазин — партнёр со своим ботом; несколько точек
одного бизнеса объединяются в сеть с одним ботом и общим входом в Admin.
Модули включаются по отдельности и настраиваются в Admin:

- каталог: подкатегории, расписание категорий, витрина, варианты и добавки,
  остатки, переводы для гостей;
- заказ за столом по QR со счетами и визитами;
- самовывоз, доставка, отправка почтой;
- оплата переводом заранее и/или при получении, возвраты;
- бонусы и кешбэк (в сети — по выбору общий баланс);
- вызов персонала, быстрые продажи, отчёт за день;
- рассылки и источники переходов;
- Telegram Mini App для гостя, рабочий экран персонала, экран кухни и бара;
- схема зала, заказ на стол с планшета, бронь столов;
- приглашения сотрудников ссылкой;
- языки бота и Admin: украинский, русский, английский.

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
  для Claude — [`CLAUDE.md`](CLAUDE.md), решения по областям кода —
  [`.claude/rules/`](.claude/rules/), порядок изменения —
  [`.claude/skills/tableos-development/SKILL.md`](.claude/skills/tableos-development/SKILL.md)
- Находки аудита и статус батчей:
  [`docs/audits/ARCHITECTURE_AUDIT.md`](docs/audits/ARCHITECTURE_AUDIT.md) и
  [`docs/audits/REFACTOR_ROADMAP.md`](docs/audits/REFACTOR_ROADMAP.md)
- Открытые продуктовые решения:
  [`docs/product/OPEN_PRODUCT_QUESTIONS.md`](docs/product/OPEN_PRODUCT_QUESTIONS.md)

Исторические документы (старое ТЗ, шпаргалка, задание первого аудита) — в
[`docs/legacy/`](docs/legacy/README.md). Связь старого ТЗ с текущим кодом —
[`docs/product/LEGACY_TZ_MIGRATION_MAP.md`](docs/product/LEGACY_TZ_MIGRATION_MAP.md).

## Проверка локально

Для быстрого знакомства можно явно включить SQLite через
`DJANGO_USE_SQLITE=true`; для поведения блокировок и ограничений как в
продакшене нужен PostgreSQL.

Команды ниже — для виртуального окружения `.venv`; без него то же самое через
`python3` (как в `CLAUDE.md`).

```bash
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py check
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py makemigrations --check --dry-run
env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py test
docker compose config --quiet
```

Полная настройка, набор процессов и текущее состояние линтера описаны в
[`docs/engineering/`](docs/engineering/), а не дублируются здесь. Никогда не
кладите в документацию настоящие токены ботов, пароли и секреты окружения.
