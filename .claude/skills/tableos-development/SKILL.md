---
name: tableos-development
description: Порядок изменения в Botera Shop от падающего теста до коммита — найти модуль-владельца и все входы, тест сначала, правка в сервисе, проверка Admin/бота/Mini App/Celery, мутации, полные прогоны SQLite и PostgreSQL 16, миграции, Ruff, переводы, документы в том же коммите. Использовать перед правкой кода приложений, моделей, миграций, бота, Mini App, Admin, задач и перед каждым коммитом с изменением поведения.
---

# Изменение в Botera Shop: от теста до коммита

Правила — в `AGENTS.md`, `CLAUDE.md` и в `.claude/rules/` своей области.
Здесь — только порядок действий и точные команды. Это не разрешение на
широкую чистку или переписывание архитектуры.

## 1. До правки

- Найти модуль-владельца и все входы: `rg -n "<имя>" apps bot webapp core tests`.
  Динамические потребители: регистрация в `admin.py`, роутеры в
  `bot/main.py:create_dispatcher`, `webapp/urls.py`, расписание beat в
  `core/settings/base.py`, management-команды, строки в `core/settings/`.
- До кода записать: инвариант; откуда берутся партнёр и исполнитель; границу
  транзакции; порядок блокировок (`.claude/rules/locks.md`);
  идемпотентность; что уходит в Telegram после коммита.
- Смысл не решён владельцем (PQ открыт) — остановиться после
  характеризующего теста и спросить.

## 2. Сначала тест

- Файл — `tests/<путь модуля>/test_*.py`. Запустить и убедиться, что он
  падает по нужной причине:
  `DJANGO_USE_SQLITE=true python3 manage.py test tests.apps.<приложение>.<модуль> --noinput`.
- Блокировки и ограничения базы — тест на PostgreSQL:

  ```python
  @skipUnless(connection.vendor == "postgresql", "нужны построчные блокировки")
  class ...(TransactionTestCase):
  ```

## 3. Правка

- Самое узкое изменение в сервисе-владельце; бот, Mini App, Admin и задачи
  только вызывают его.
- Проверить каждый другой путь записи: формы, действия и inline Admin,
  обработчики бота, запросы Mini App, задачи Celery, management-команды.
- Новые тексты — `.claude/rules/i18n.md`: после них
  `python3 infrastructure/scripts/messages.py update`, перевести uk и en,
  `python3 infrastructure/scripts/messages.py compile`.
- Новая модель или поле — миграция, `verbose_name` по-русски.

## 4. Мутации

Убрать защиту (проверку, блокировку, условие) → нужный тест падает; вернуть →
проходит. Список мутаций — в строку журнала `TESTING.md`.

## 5. Проверка перед коммитом

```bash
DJANGO_USE_SQLITE=true python3 manage.py check
DJANGO_USE_SQLITE=true python3 manage.py makemigrations --check --dry-run
DJANGO_USE_SQLITE=true python3 manage.py test --noinput                           # весь набор, SQLite
DJANGO_USE_SQLITE=false POSTGRES_HOST=127.0.0.1 python3 manage.py test --noinput  # весь набор, PostgreSQL 16
```

- Не отвечают PostgreSQL или Redis (облачная среда) — `service postgresql start`,
  `service redis-server start`.
- Миграции — вперёд/назад/вперёд на PostgreSQL:
  `… migrate <app>`, `… migrate <app> <номер предыдущей>`, `… migrate <app>`
  (с `DJANGO_USE_SQLITE=false POSTGRES_HOST=127.0.0.1`).
- Ruff — новых замечаний в изменённых файлах быть не должно. Сравнивать
  одной версией Ruff (AA-053), без `git stash`:

  ```bash
  git show HEAD:<файл> | ruff check --no-cache --stdin-filename <файл> --output-format concise -   # до
  ruff check --no-cache <файл> --output-format concise                                            # после
  ```

  В новых файлах замечаний нет совсем. Старые замечания заодно не чинить.
- Менялась конфигурация развёртывания — `docker compose config --quiet`.

## 6. Документы — в том же коммите

По `.claude/rules/docs.md`. Строка журнала `docs/engineering/TESTING.md`:
`| дата | что сделано (ветка, PQ), тесты, мутации, миграции | SQLite / PostgreSQL 16 | объём | итог: число тестов, OK |`.

## 7. Коммит и отчёт владельцу

- Небольшой зелёный коммит; сообщение по-английски, как в `git log`
  (`Floor plan, step 3: table reservations (PQ-052)`, `docs(audit): …`).
- Пушить — когда просит владелец; в `clean-project-rules` — только по его
  слову.
- Владельцу: что сделано и как проверить руками; какие команды прогнаны и
  их итог (число тестов, OK); что осталось открытым или ждёт его решения.

## Чек-лист

- [ ] Найдены модуль-владелец и динамические потребители.
- [ ] Права партнёра и исполнителя проверены в каждом входе.
- [ ] Транзакция, блокировки, идемпотентность и граница ввода-вывода явные.
- [ ] Тест сначала падал; мутации проверены.
- [ ] Полный прогон SQLite и PostgreSQL 16 — OK; миграции вперёд/назад/вперёд.
- [ ] Ruff без новых замечаний; переводы обновлены.
- [ ] Документы и журнал `TESTING.md` — в этом же коммите.
