# Botera Shop — руководство разработчика

**Статус:** проверено по коду. Точные результаты базовой проверки — в
[`../audits/BASELINE.md`](../audits/BASELINE.md).

## Что нужно

- Python 3.10 или новее (на аудите использовался Python 3.10.20).
- PostgreSQL — для данных как в продакшене, миграций, ограничений и проверки
  конкурентности.
- Redis и воркер Celery — для фоновой доставки.
- Действующая зашифрованная настройка Telegram-бота для настоящего polling;
  токены-заглушки из сидов ботом не являются.

Проект описан как пакет PEP 621/Hatchling в `pyproject.toml`; lock-файла в
репозитории нет. Используйте виртуальное окружение проекта или аналогичное
изолированное, и не записывайте личные и продакшен-секреты в документацию,
историю команд или фикстуры.

## Первая настройка (macOS / Linux)

```bash
git clone https://github.com/Key-pi/tableos.git && cd tableos
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# Сгенерируйте ключ Fernet и впишите его в BOT_TOKEN_ENCRYPTION_KEY в .env:
./.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`core/settings/env.py` читает `.env` из рабочей папки; настоящие переменные
окружения его перекрывают. По умолчанию `.env.example` настроен на
PostgreSQL (`DJANGO_USE_SQLITE=false`), поэтому либо запустите
`docker compose up -d db redis`, либо добавляйте к командам
`DJANGO_USE_SQLITE=true`.

## Локальный запуск

SQLite допустим только для быстрого знакомства:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py migrate
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runserver
```

Для поведения с `select_for_update`, частичными ограничениями или
параллельными воркерами настройте PostgreSQL. Успех на SQLite не доказывает,
что блокировки работают как в продакшене.

Полезные процессы запускаются отдельно:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runserver
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py runbot
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery worker --loglevel=info
```

Запланированным рассылкам, восстановлению зависших рассылок и снимкам
метрик дня нужен ещё Celery beat:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/celery -A core.celery beat --loglevel=info
```

`docker compose up` поднимает все нужные процессы: `db`, `redis`, `web`,
`worker`, `beat` и `bot` (с B4). `beat` и `bot` — ровно по одному.
Процессы кроме web используют `core.settings.local` (`DEBUG=True`), если не
задан `DJANGO_SETTINGS_MODULE`.

## Сиды и локальные данные

Management-команды создают демо- и базовые данные. Все выданные ими логины —
только для локальной работы, не постоянные учётные данные для документации.
`seed_demo` выполняется одной транзакцией и повторный запуск безопасен
(AA-019); не используйте сиды как неявную миграцию продакшен-данных.

## Порядок изменения

1. Прочитать [`AGENTS.md`](../../AGENTS.md), [`CLAUDE.md`](../../CLAUDE.md)
   и документ нужного модуля/архитектуры.
2. Найти статические импорты и динамические регистрации (Django Admin,
   миграции, Celery, роутеры aiogram, management-команды) прежде чем
   переносить или удалять код.
3. Добавить или обновить точечный тест (характеризующий или регрессионный)
   прежде чем менять инвариант или описанное поведение.
4. Держать в сервисе-владельце явными: границы партнёра, права исполнителя,
   транзакции и порядок блокировок, внешние побочные эффекты.
5. Выполнить нужные проверки ниже и обновить канонические документы/ADR,
   если изменилось правило или решение.

## Обязательные границы реализации

- Обработчики бота, действия Admin и задачи Celery — адаптеры: вызывают
  сервисы и селекторы владельца, а не меняют напрямую операционное и
  денежное состояние.
- Никакого сетевого ввода-вывода, пауз перед повтором и рассылки внутри
  `transaction.atomic`.
- Не считать данные callback, видимость в интерфейсе или фильтрацию списка
  доказательством прав на партнёра.
- Сохранять публичные импорты и имена задач, если утверждённый план
  совместимости не говорит иное.
- Не добавлять repository/interface/policy/engine только ради шаблона папок;
  правила слоёв — в
  [`../architecture/LAYERING_RULES.md`](../architecture/LAYERING_RULES.md).
