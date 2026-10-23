# Базовая проверка аудита — 2026-07-27

> **Исторический снимок** аудита 2026-07-27 на коммите `b17ce5b`. Сохранён как свидетельство и не обновляется при изменении кода. Батчи B1–B3 сообщали об успешных прогонах на SQLite без чисел и вывода; полные прогоны с B3.1 записываются в [`../engineering/TESTING.md`](../engineering/TESTING.md), там же — как запускать проверки сегодня.

## Состояние репозитория

| Пункт | Наблюдаемое значение |
| --- | --- |
| Корень git | `/Users/danil/projects/tableos` |
| Ветка | `clean-project-rules` |
| HEAD | `b17ce5b4e52b0fef4db49b3171ecf516c70d22b8` (`Merge pull request #1 from Key-pi/refactor-bot-content`) |
| Ранее существовавшие изменения рабочей копии | подготовленное удаление `SKILLS.md`; неотслеживаемый `TABLEOS_CODEBASE_AUDIT_MASTER_PROMPT.md` |
| Изменения аудита | Только материалы документации и правил. Python приложения, миграции, импорты, зависимости, конфигурация процессов не менялись; коммитов и пушей не было. |

`git diff --check` и `git diff --cached --check` были чистыми до записи документов аудита. Удалённый `SKILLS.md` не восстанавливался и не использовался как действующий файл инструкций.

На момент базы не было ни `AGENTS.md`, ни `AGENTS.override.md`, ни каталогов `.codex/` или `.agents/`. Поэтому в корне репозитория в начале аудита не было дополнительных инструкций для агентов.

## Окружение и управление зависимостями

| Область | Доказательство |
| --- | --- |
| Упаковка | PEP 621 в `pyproject.toml`; сборщик Hatchling; нет lock-файла и манифестов requirements/Poetry/uv/Pipenv |
| Объявленный Python | `>=3.10` |
| Активный Python | `.venv/bin/python` → Python 3.10.20 |
| Активный Django | 5.2.14 (объявлено `>=5.1,<6.0`) |
| Активный aiogram | 3.28.2 (объявлено `>=3.7,<4.0`) |
| Активный Celery | 5.6.3 (объявлено `>=5.4,<6.0`) |
| Активный Ruff | 0.13.3 |
| Проверка типов | Конфигурации и исполняемых файлов mypy/pyright не найдено |

`python3` вне `.venv` не содержал Django, поэтому все задокументированные проверки Django должны использовать `.venv/bin/python` (или эквивалентное активированное окружение).

## Режимы базы и процессы

`core/settings/env.py` по умолчанию выбирает PostgreSQL, а SQLite — только при `DJANGO_USE_SQLITE=true`. На базе `docker-compose.yml` объявлял PostgreSQL 16, Redis, веб Django и воркер Celery. Сервисов `runbot` и Celery beat в нём не было, хотя `core/settings/base.py` настраивает задания beat, а рабочие документы требуют оба процесса для полной работы (добавлены в B4).

Локальное окружение использовало SQLite (`DJANGO_USE_SQLITE=true`). Существующий игнорируемый `db.sqlite3` проверками не менялся. Тесты создавали и удаляли свою тестовую базу.

Найденные процессы:

- HTTP Django и Admin: `manage.py runserver`;
- polling Telegram: `manage.py runbot`;
- воркер Celery с очередями `default` и `broadcasts`;
- Celery beat для запланированных кампаний и метрик дня;
- PostgreSQL и Redis в продакшене и общих окружениях.

## Базовая проверка

Команды запускались без установки зависимостей. `PYTHONDONTWRITEBYTECODE=1` и `--no-cache` у Ruff добавлены только чтобы аудит не создавал кэш-файлы.

| Команда | Результат |
| --- | --- |
| `env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py check` | PASS — `System check identified no issues (0 silenced).` |
| `env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py makemigrations --check --dry-run` | PASS — `No changes detected` |
| `env DJANGO_USE_SQLITE=true PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python manage.py test` | PASS — 139 тестов за 8,420 с |
| `./.venv/bin/ruff check --no-cache .` | FAIL — 21 старое замечание: E501 ×11, I001 ×7, F401 ×3; 10 исправимы автоматически |
| `./.venv/bin/ruff format --check --no-cache .` | FAIL — 54 файла были бы переформатированы; 167 уже отформатированы |
| `docker compose config --quiet` | PASS |

Ошибки Ruff были в закоммиченных файлах приложения ещё до аудита. Затронуты, в том числе, `apps/analytics/tests.py`, `apps/billing/tests.py`, `apps/notifications/services.py`, `apps/notifications/tests.py`, `apps/orders/tests.py`, `bot/handlers/profile.py`, `bot/handlers/staff.py`, `bot/tests_menu.py` и `bot/tests_staff.py`. В режиме аудита их намеренно не исправляли.

## Расхождения документации на базе

На базе аудита `PROJECT_CHEATSHEET.md` утверждал, что текущая база — «66 tests OK» и «Ruff all checks passed». Точный прогон выше дал 139 проходящих тестов и 21 замечание Ruff на неизменённой рабочей копии приложения. Правка документации после базы теперь указывает на эту запись; исходное противоречие остаётся записанным как `DOC_DRIFT` в [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md), а не стёрто молча.

## Ограничения для следующих реализаций

Любой батч реализации должен начинаться с сохранения ранее существовавшего удаления `SKILLS.md` и неотслеживаемого мастер-промпта, повторного запуска нужных команд выше и сравнения результата с этой базой. Поведению, специфичному для PostgreSQL, нужна отдельная стратегия характеризации и CI; успешные проверки на SQLite не доказывают таких же ограничений и поведения конкуренции в продакшене.
