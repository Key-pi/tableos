# CLAUDE.md — Botera Shop (бывший TableOS)

Общие правила репозитория — в `AGENTS.md`, они действуют полностью:

@AGENTS.md

Ниже — то, что нужно знать поверх них: текущее состояние, решения
владельца и практика работы. Источник правды по поведению — код и
канонические документы в `docs/`; этот файл только указывает, где что.

## Что это за проект

- **Botera** — сеть продуктов владельца. Этот репозиторий — **Botera Shop**:
  Telegram-бот и Django Admin одного бизнеса (заведение или магазин),
  multi-tenant (много партнёров в одной базе, у каждого свой бот).
- **Botera Master** (мастера) — отдельный проект, уже в разработке.
  **Partnership** и **Discovery** — будущие отдельные сервисы. Никаких
  импортов их моделей и общих таблиц; связь только через API/события с
  `event_id`, и они никогда не обязательны для заказа или оплаты.
  Подробно: `docs/product/ECOSYSTEM_DIRECTION.md`.
- Имя «TableOS» уходит из всего, что видят люди. Внутренние имена в коде и
  базе (`tableos` в `pyproject.toml`, пакеты, таблицы) не трогаем.
- **Стол — один из каналов заказа**, не основа: `Order.channel` = `table` /
  `pickup` / `delivery`; дальше отправка почтой. Способ заказа гость
  выбирает сначала (кнопка или QR стола), он хранится в `Cart.channel`; у
  позиций меню отметки, в каких способах они продаются.
- **ADR-0009:** одна модель заказа/счёта/оплаты для всех каналов; никаких
  «ЗаказДоставки»/«СчётДоставки». Данные канала — поля заказа
  (`requested_for`, `delivery_*`), поведение — модуль на семейство: стол —
  `apps/tables` + `apps/billing/table_bills.py`; удалённые каналы —
  `apps/billing/remote_orders.py` (`REMOTE_CHANNELS`,
  `Order.REMOTE_CHANNELS`) и бот `bot/handlers/remote_checkout.py`. Ядро
  счёта `apps/billing/services.py` про каналы не знает. Различать каналы по
  `order.channel`, а не по `order.table_id`.

## Карта документов (что читать перед работой)

| Задача | Читать |
|---|---|
| Что реально работает сейчас | `docs/architecture/AS_IS_ARCHITECTURE.md` |
| Продукт и статус фич | `docs/product/PRODUCT_SPEC.md`, `FEATURE_MATRIX.md` |
| Решения владельца и открытые вопросы | `docs/product/OPEN_PRODUCT_QUESTIONS.md` (PQ-…) |
| Направление сети Botera | `docs/product/ECOSYSTEM_DIRECTION.md` |
| Визиты стола, правка/аннулирование счёта, кешбэк | `docs/architecture/B3_2_VISITS_AND_BILL_CHANGES.md`, ADR-0007 |
| Каналы заказа (стол, самовывоз, доставка) | ADR-0009, `docs/architecture/PICKUP.md`, `DELIVERY.md`, `PAYMENT_OPTIONS.md` |
| Доставка уведомлений и рассылок | `docs/architecture/BACKGROUND_JOBS.md`, ADR-0008 |
| Бот, callback-и | `docs/architecture/BOT_RUNTIME.md` |
| Находки аудита и что сделано по батчам | `docs/audits/ARCHITECTURE_AUDIT.md`, `REFACTOR_ROADMAP.md` |
| Тесты и журнал прогонов | `docs/engineering/TESTING.md` |

`TABLEOS_TZ.md` и `PROJECT_CHEATSHEET.md` — исторические, ниже по
приоритету, чем код и `docs/`.

## Решения владельца, которые легко нарушить

- **Визиты (PQ-026, ADR-0007):** счёт, кнопка «Запросить счёт» и
  «стол рассчитан» считаются по визиту стола, не по истории стола.
- **Автозакрытие (PQ-027):** закрываются только полностью оплаченные сессии;
  шаг автозакрытия никогда не откатывает оплату.
- **Счета (PQ-028):** менять/аннулировать может владелец или сотрудник с
  правом от владельца; всё в `BillAdjustment`; итог не ниже оплаченного;
  счёт с бонусами только аннулируется (бонусы возвращаются `reversal`).
- **Гость подтверждает получение (PQ-029)** из любого нефинального статуса.
- **Кешбэк (PQ-030):** база `full_total` или `money_paid` на программу.
- **Источники переходов (PQ-031):** гость закрепляется за первым источником,
  все переходы пишутся; источники создаёт владелец в Admin; цифры — только
  переходы и новые гости, только в Admin.
- **Доставка сообщений (ADR-0008):** рассылки — не больше одного раза на
  гостя (`BroadcastDelivery`); рабочие уведомления — хотя бы один раз.
- **Модули настраиваемые (PQ-033):** владелец включает варианты, гость
  выбирает из включённых. Оплата удалённых заказов — «переводом заранее»
  и/или «при получении» на модуль (`Order.payment_timing`); не зашивать
  один вариант в канал.
- **Самовывоз:** перевод на реквизиты заведения (подтверждает
  владелец/менеджер/кассир) и/или оплата на месте, бонусы по желанию,
  телефон обязателен, только в часы работы (`OpeningHours`).
- **Доставка (PQ-032):** то же, что самовывоз, плюс адрес (текст
  обязателен, геолокация по желанию, последний запоминается); цена —
  фикс, бесплатно от суммы, минимальный заказ; цена — строка заказа
  `OrderItem.kind = delivery_fee` без позиции меню; персонал отмечает
  «в пути» и «доставлено».
- Новый продуктовый смысл без решения владельца не выбирать — спросить.

## Порядок блокировок (не менять без причины)

- Оплата: `Bill → Order → GuestProfile → BillingRequest → TableSession → TableVisit`.
- Активация стола: `GuestProfile → Table → свои TableSession → TableVisit`.
- Изменение счёта: `Bill → BillItem → GuestProfile → Order`.
- На PostgreSQL `select_for_update` с nullable-join только с `of=("self",)`.

## Как работаем

- Ветка: `clean-project-rules`. Коммитить небольшими зелёными шагами;
  пушить в неё, когда владелец просит.
- Сначала тест, который падает; для блокировок — тест на PostgreSQL
  (`TransactionTestCase`, `skipUnless(connection.vendor == "postgresql")`) и
  проверка мутацией (убрать защиту → тест падает).
- Полный прогон на SQLite **и** PostgreSQL 16 перед каждым коммитом с
  изменением поведения; миграции — вперёд/назад/вперёд на PostgreSQL.
- Ruff: не добавлять новых нарушений в изменённых файлах (сравнивать с
  `git stash`); старые не чинить заодно.
- Документы обновлять в том же коммите: `AS_IS_ARCHITECTURE.md`,
  `FEATURE_MATRIX.md`, PQ/ADR при решении, строка прогона в `TESTING.md`.
  Число тестов также упоминается в `AGENTS.md`, `.agents/skills/…/SKILL.md`,
  `PROJECT_CHEATSHEET.md`, AS-IS — менять везде.
- Тексты для людей (бот, Admin) и документация — по-русски; сообщения бота в HTML, все
  динамические значения через `escape_html`.
- Telegram callback_data ≤ 64 байт: UUID в виде hex, состояние шагов — в
  самом callback (без FSM, где можно).

## Команды

```bash
# тесты, SQLite (нужен .env с BOT_TOKEN_ENCRYPTION_KEY — ключ Fernet)
python3 manage.py test --noinput

# тесты, PostgreSQL 16
DJANGO_USE_SQLITE=false POSTGRES_HOST=127.0.0.1 python3 manage.py test --noinput

python3 manage.py makemigrations --check --dry-run
ruff check --no-cache <изменённые файлы> --output-format concise
docker compose config --quiet

# весь стек в Docker (пошагово: docs/engineering/DEVELOPMENT.md)
cp .env.example .env && docker compose up --build
docker compose logs init        # логины и ссылки на столы демо-данных
```

Подводные камни окружения: таймзона по умолчанию `Europe/Kyiv` (старое
`Europe/Kiev` в `.env` читается как Kyiv, но локальный PostgreSQL с
`timezone = 'Europe/Kiev'` в `postgresql.conf` не стартует); Celery в тестах
eager; `docker compose up` поднимает db, redis, разовый init (миграции +
`seed_baseline_demo --keep-existing`), затем web, worker, beat, bot
(`runbot --wait`) — beat и bot ровно по одному. Демо-данные берут токены
ботов и Telegram ID владельца из `.env` (`DEMO_*`).

## Что дальше (на 2026-10-23)

Видимое уже переименовано в Botera Shop, вся документация — на русском,
источники переходов (`start=src_…`, PQ-031) и доставка (PQ-032) сделаны.

1. Отправка почтой; интеграция с Partnership; карточка для Discovery;
   самостоятельное подключение бизнеса.
