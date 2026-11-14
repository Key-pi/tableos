<!--
Заметка для тех, кто правит инструкции. Блочные HTML-комментарии вырезаются
до загрузки в контекст: Claude этот текст не видит и токены на него не тратит.

Как устроено (https://code.claude.com/docs/en/memory):
- AGENTS.md — общие правила для всех агентов; этот файл его импортирует.
- CLAUDE.md — только то, что нужно в каждой сессии и чего не видно из кода:
  команды проверки, решения владельца, которые действуют везде, как мы
  работаем, подводные камни. Держать короче 200 строк.
- .claude/rules/<область>.md — решения и соглашения одной области кода; поле
  paths: — к каким файлам относится, Claude подгружает файл, когда читает
  такой файл. Новое решение по одной области — туда, не сюда.
- .claude/skills/tableos-development/SKILL.md — порядок изменения и
  проверки по шагам (процедура, а не факт). .agents/skills/ — указатель на
  него для других агентов.
- История веток — в docs/audits/REFACTOR_ROADMAP.md и PQ, не здесь.
- Личные заметки — CLAUDE.local.md (в .gitignore).

После сжатия разговора (https://code.claude.com/docs/en/context-window):
этот файл и AGENTS.md перечитываются с диска; правила с paths: уходят в
пересказ и вернутся, только когда Claude снова откроет такой файл. Поэтому
то, что должно действовать без чтения кода области, — здесь, а не в правиле.
Навык после сжатия возвращается обрезанным (до 5 000 токенов, начало
файла) — главное держать вверху SKILL.md.

Что загрузилось — /memory и /context; устаревшее и противоречия —
/doctor prompt-audit.
-->

# CLAUDE.md — Botera Shop (бывший TableOS)

@AGENTS.md

Выше — общие правила репозитория, они действуют полностью. Ниже — то, что
нужно поверх них. Источник правды по поведению — код и `docs/`.

## Что это за проект

- Botera — сеть продуктов владельца. Этот репозиторий — Botera Shop:
  Telegram-бот, Mini App и Django Admin одного бизнеса (заведение или
  магазин); multi-tenant — много партнёров в одной базе, у каждого свой бот.
- Botera Master — отдельный проект; Partnership, Discovery и общий бот
  платформы Hub — будущие отдельные сервисы. Никаких импортов их моделей и
  общих таблиц: только API/события с `event_id`, и для заказа или оплаты они
  никогда не обязательны (`docs/product/ECOSYSTEM_DIRECTION.md`).
- Имя «TableOS» убираем из всего, что видят люди. Внутренние имена (`tableos`
  в `pyproject.toml`, пакеты, таблицы, имя навыка) не трогаем.
- Стол — один из каналов заказа: `Order.channel` = `table` / `pickup` /
  `delivery` / `shipping`. Гость сначала выбирает способ (кнопка или QR
  стола), он хранится в `Cart.channel`; у позиций меню — отметки, в каких
  способах они продаются.
- Одна модель заказа, счёта и оплаты на все каналы (ADR-0009), без
  «ЗаказДоставки». Данные канала — поля заказа (`requested_for`,
  `delivery_*`, `shipping_*`); стол — `apps/tables` +
  `apps/billing/table_bills.py`; удалённые каналы —
  `apps/billing/remote_orders.py` (`Order.REMOTE_CHANNELS`) и
  `bot/handlers/remote_checkout.py`; ядро `apps/billing/services.py` про
  каналы не знает. Каналы различать по `order.channel`, не по `order.table_id`.

## Решения владельца, которые действуют везде

- Новый продуктовый смысл без решения владельца не выбирать — спросить.
  Решения и открытые вопросы — `docs/product/OPEN_PRODUCT_QUESTIONS.md` (PQ-…).
- Модули — конструктор (PQ-033): каждый включается отдельно, настраивается
  своими полями и не меняет поведение других (любой набор, в том числе
  один модуль).
- Гость визита может быть анонимным (PQ-052): `GuestProfile.telegram_account`
  пустой — код, который пишет гостю, шлёт рассылки или считает бонусы,
  должен это учитывать.
- Сети (PQ-047): действия за точку — `acts_for_partner` / `owns_partner`, не
  `user.partner_id`; баланс бонусов — `bonus_balance(guest)`, гость по коду —
  `find_guest_by_customer_code`.
- Языки (PQ-040): по умолчанию украинский; тексты для людей пишутся в коде
  по-русски внутри `_()` (`.claude/rules/i18n.md`).
- Данные гостей хранятся, гость меняет их сам (PQ-024). Мониторинг ошибок —
  будущий бот Hub (PQ-039), сейчас не делаем.

## Правила по областям кода

Решения и соглашения одной области лежат в `.claude/rules/`. Claude Code
подгружает файл, когда читает подходящий код (поле `paths:`). Если правила
области нет в контексте (другой агент) — прочитать файл перед правкой.

| Область | Файл в `.claude/rules/` |
|---|---|
| Визиты, счета, оплата, кешбэк, возвраты | `money.md` |
| Самовывоз, доставка, почта, оплата по модулю, часы | `order-channels.md` |
| Права владельца, сотрудники, приглашения | `staff.md` |
| Сети заведений | `networks.md` |
| Бот: сервис ботов, callback-и, HTML, путь гостя | `bot.md` |
| Mini App, экран кухни и бара | `mini-app.md` |
| Каталог: витрина, варианты, остатки, переводы | `catalog.md` |
| Admin | `admin.md` |
| Схема зала, заказ на стол, бронь | `floor-plan.md` |
| Источники переходов, рассылки, уведомления | `marketing.md` |
| Нагрузка, подключение бизнеса, демо, продакшен | `platform.md` |
| Тексты и языки | `i18n.md` |
| Порядок блокировок | `locks.md` |
| Тесты | `tests.md` |
| Документация и сами инструкции | `docs.md` |

## Как работаем

- Работа — в отдельной ветке от `clean-project-rules`. Большая фича — по
  шагам, каждый шаг проверяет владелец до следующего.
- Пушить — когда просит владелец. Сливать в `clean-project-rules` — только по
  его слову; после слияния — коммит документов «… checked by the owner;
  <ветка> merged».
- Коммитить небольшими зелёными шагами. Порядок изменения по шагам — навык
  `.claude/skills/tableos-development/SKILL.md` (не подгрузился — прочитать
  файл).
- Сначала тест, который падает; для блокировок — тест на PostgreSQL и
  проверка мутацией (убрать защиту → тест падает).
- Перед каждым коммитом с изменением поведения — полный прогон на SQLite и
  на PostgreSQL 16; миграции — вперёд/назад/вперёд на PostgreSQL. Владельцу
  сообщать команды и их итог, а не «всё зелёное».
- После пуша GitHub Actions (`.github/workflows/tests.yml`) гоняет то же
  самое; результат — `gh run list --branch <ветка>`. Красный — чинить до слияния.
- Ruff: в изменённых файлах не добавлять новых замечаний; старые заодно не
  чинить.
- Документы — в том же коммите (что обновлять — `.claude/rules/docs.md`).
  Расхождения с кодом ловит `tests/core/test_docs.py`; при коммите только с
  документами — `python3 infrastructure/scripts/check_docs.py`.
- Документация — по-русски; сообщения коммитов — по-английски, как в истории.
- Не писать настоящие токены, пароли, ключи и Telegram ID в код, тесты,
  документы и коммиты — только заглушки.
- Правило на будущее, которое владелец дал в разговоре, — предложить
  записать сюда или в правило области: из разговора оно пропадёт после
  сжатия.

## Команды

```bash
# часть набора / весь набор на SQLite
DJANGO_USE_SQLITE=true python3 manage.py test tests.apps.billing --noinput
DJANGO_USE_SQLITE=true python3 manage.py test --noinput

# весь набор на PostgreSQL 16
DJANGO_USE_SQLITE=false POSTGRES_HOST=127.0.0.1 python3 manage.py test --noinput

DJANGO_USE_SQLITE=true python3 manage.py makemigrations --check --dry-run
python3 infrastructure/scripts/check_docs.py         # документация против кода
ruff check --no-cache <изменённые файлы> --output-format concise
python3 infrastructure/scripts/messages.py update    # затем перевести и … compile
docker compose config --quiet

# весь стек в Docker — docs/engineering/DEVELOPMENT.md; продакшен — DEPLOYMENT.md
cp .env.example .env && docker compose up --build
docker compose logs init        # логины и ссылки на столы демо-данных
```

## Подводные камни

- Без `DJANGO_USE_SQLITE=true` тесты идут в PostgreSQL: по умолчанию `false`
  (`core/settings/env.py`, `.env.example`). Нужен `.env` с
  `BOT_TOKEN_ENCRYPTION_KEY` (ключ Fernet).
- `.env`, `.env.production` и `infrastructure/loadtest/.local.env` Claude не
  читает и не правит — запрет в `.claude/settings.json` (там токены и ключи).
  Имена переменных — в `.env.example`; значения владелец вписывает сам, в чат
  их не просить.
- После сжатия разговора хук из `.claude/settings.json` добавляет
  `git status` и коммиты ветки, которых нет в `origin/clean-project-rules`.
- SQLite игнорирует `select_for_update` — блокировки проверяет только
  PostgreSQL. Celery в тестах eager.
- Таймзона `Europe/Kyiv`. Старое `Europe/Kiev` в `.env` читается как Kyiv, но
  локальный PostgreSQL с `timezone = 'Europe/Kiev'` в `postgresql.conf` не
  стартует.
- Ruff в окружении новее, чем в `pyproject.toml` (AA-053): «до» и «после»
  сравнивать одной версией.
- `docker compose up`: db, redis, разовый init (миграции +
  `seed_baseline_demo --keep-existing`), затем web, worker, beat, bot
  (`runbot --wait`); beat и bot — ровно по одному (процессы бота —
  `--processes` / `BOT_PROCESSES` внутри сервиса). Демо-данные берут токены
  ботов и Telegram ID владельца из `.env` (`DEMO_*`).
- `bot/main.py:run_polling` — только для эмулятора и нагрузочного теста; в
  работе боты запускает `bot/supervisor.py`.
- Помощники Explore и Plan стартуют без `CLAUDE.md` и правил — называть их в
  задании. `docs/legacy/` — исторические документы, ниже кода и `docs/`.

## Карта документов

| Задача | Читать |
|---|---|
| Что реально работает сейчас | `docs/architecture/AS_IS_ARCHITECTURE.md` |
| Продукт и статус фич | `docs/product/PRODUCT_SPEC.md`, `FEATURE_MATRIX.md` |
| Решения владельца и открытые вопросы | `docs/product/OPEN_PRODUCT_QUESTIONS.md` |
| Направление сети Botera | `docs/product/ECOSYSTEM_DIRECTION.md` |
| Визиты, правка и аннулирование счёта, кешбэк | `docs/architecture/B3_2_VISITS_AND_BILL_CHANGES.md`, ADR-0007 |
| Каналы заказа | ADR-0009, `docs/architecture/PICKUP.md`, `DELIVERY.md`, `SHIPPING.md`, `PAYMENT_OPTIONS.md` |
| Возвраты | `docs/architecture/REFUNDS.md` |
| Языки бота и Admin | `docs/architecture/LANGUAGES.md` |
| Уведомления и рассылки | `docs/architecture/BACKGROUND_JOBS.md`, ADR-0008 |
| Бот, callback-и | `docs/architecture/BOT_RUNTIME.md` |
| Сети заведений | `docs/architecture/NETWORKS.md` |
| Mini App, экран кухни и бара | `docs/architecture/MINI_APP.md` |
| Каталог | `docs/architecture/CATALOG.md` |
| Схема зала, заказ на стол, бронь | `docs/architecture/FLOOR_PLAN.md` |
| Находки аудита, что сделано по веткам | `docs/audits/ARCHITECTURE_AUDIT.md`, `REFACTOR_ROADMAP.md` |
| Тесты и журнал прогонов | `docs/engineering/TESTING.md` |
| Нагрузка: сколько партнёров держит сервер | `docs/engineering/LOAD_TESTING.md`, `infrastructure/loadtest/` |
| Запуск локально и в продакшене | `docs/engineering/DEVELOPMENT.md`, `DEPLOYMENT.md` |

## Где мы сейчас (2026-11-14)

Что сделано по веткам — `docs/audits/REFACTOR_ROADMAP.md` («После дорожной
карты»); открытое — `docs/audits/ARCHITECTURE_AUDIT.md` («Остаются
открытыми»): AA-053 (версия Ruff). Решено 2026-11-14, ещё не сделано:
бонусы — модулем, у нового заведения все модули выключены (PQ-001,
PQ-053); пустые сессии стола закрываются сами через 4 ч (PQ-008).

Дальше:

1. Импорт каталога (Excel/CSV) — когда владелец решит; вопрос — что делать с
   позициями, которые уже есть.
2. Подключение бизнеса в Shop (PQ-045): создание бизнеса одной командой/API,
   вход владельца в Admin по одноразовой ссылке, сообщение владельцу
   платформы о новом бизнесе.
3. Интеграция с Partnership; карточка для Discovery; Hub — вместе с Master.
4. При росте базы — настройка памяти PostgreSQL (`DEPLOYMENT.md`); повтор
   нагрузочного теста при 15–20 партнёрах.

## Compact Instructions

При сжатии разговора сохранить: текущую ветку, что запушено и что слито;
чего ждём от владельца и его последние решения дословно; итог последних
полных прогонов (команда, число тестов, OK/FAIL); незакоммиченные изменения и
следующий шаг.
