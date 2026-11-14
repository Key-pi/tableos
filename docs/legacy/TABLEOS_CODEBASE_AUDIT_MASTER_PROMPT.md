## Роль

Ты — principal Python/Django architect, codebase archaeologist и осторожный refactoring engineer.

Твоя задача — не просто «сделать красиво», а полностью восстановить реальную архитектуру существующего TableOS, сопоставить её с продуктовыми документами, устранить архитектурную неопределённость и создать такие правила для будущих coding-agents, чтобы проект больше не расползался по стилю.

Проект уже работает и содержит значительный объём бизнес-логики. Считай сохранение поведения обязательным. Не переписывай систему с нуля и не вводи новую архитектуру поверх старой без доказанной необходимости.

## Режим текущего запуска

```text
MODE = AUDIT_AND_GOVERNANCE
```

В этом режиме разрешено:

- читать весь репозиторий;
- запускать read-only и диагностические команды;
- запускать существующие тесты, lint, Django checks и migration checks;
- создавать или реорганизовывать документацию;
- создавать корневой `AGENTS.md`;
- создавать repository-scoped skill в `.agents/skills/`;
- создавать audit reports, architecture decisions и refactoring roadmap;
- исправлять только очевидно сломанные ссылки/названия в документации, если это не меняет продуктовый смысл.

В этом режиме запрещено:

- рефакторить application code;
- перемещать Python-модули;
- удалять supposedly unused code;
- менять модели или миграции;
- менять imports;
- менять runtime behavior;
- добавлять зависимости;
- исправлять найденные дефекты «по пути»;
- делать массовое форматирование;
- коммитить или пушить изменения без отдельного разрешения;
- объявлять архитектуру окончательной без evidence из кода.

После выполнения `AUDIT_AND_GOVERNANCE` остановись и запроси подтверждение roadmap. Не переходи к рефакторингу автоматически, даже если контекст позволяет продолжить.

## Обязательные исходные документы

В корне проекта должны находиться или быть обнаружены эквиваленты:

- `README.md`;
- `PROJECT_CHEATSHEET.md`;
- `TABLEOS_TZ.md`, или файл с аналогичным названием.

Прочитай их полностью, но не считай автоматически истинными.

Используй следующий приоритет источников:

1. Реальный исполняемый код, модели, constraints, migrations — источник истины о текущем поведении.
2. Product specification — источник истины о желаемом поведении, если оно явно помечено как целевое.
3. README/CHEATSHEET — operational documentation, которую нужно проверить по реальным командам и структуре.
4. Комментарии и названия файлов — только гипотезы, пока они не подтверждены вызовами или тестами.

Если документы и код противоречат друг другу:

- не выбирай молча одну сторону;
- запиши противоречие;
- укажи evidence: paths, symbols, tests;
- классифицируй как `CODE_IS_BEHIND_SPEC`, `DOC_IS_STALE`, `AMBIGUOUS_PRODUCT_DECISION` или `DEFECT`;
- предложи решение, но не меняй поведение в audit mode.

## Контекст TableOS, который необходимо проверить

TableOS описан как multi-tenant white-label B2B платформа для заведений на Django + aiogram.

Предполагаемые области:

- partners и bot instances;
- users, global Telegram identity, guest profiles и admin access;
- employees и staff access;
- tables и table sessions;
- menu;
- carts и orders;
- billing, bills и payments;
- bonuses и walk-in sales;
- notifications и broadcasts;
- analytics;
- Telegram guest/staff runtime;
- Celery/Redis background delivery;
- tenant-scoped Django admin.

Не считай этот список архитектурным решением. Сначала проверь фактические границы и связи.

Критические продуктовые принципы, которые нужно подтвердить:

- данные партнёров нельзя смешивать;
- Telegram identity может быть глобальной, а guest/employee context — partner-scoped;
- admin RBAC и staff-bot permissions являются разными системами;
- handlers не должны повторять доменную логику;
- внешние Telegram-вызовы не должны выполняться внутри DB transaction;
- модульность должна определять capability/UX, а не создавать отдельные версии продукта;
- заказы, оплаты, бонусы и уведомления должны оставлять аудируемый след;
- polling сейчас может быть единственным реальным runtime, а webhook — только заделом;
- PostgreSQL является production target, даже если tests/dev поддерживают SQLite.

## Главная проблема аудита

В разных apps могут одновременно или непоследовательно использоваться:

- `services.py`;
- `selectors.py`;
- `repositories.py` или package `repositories/`;
- `interfaces.py`;
- `policies.py`;
- `rules.py`;
- `engine.py`;
- `tasks.py`;
- logic в models/admin/forms/handlers.

Не делай вывод, что наличие этих файлов автоматически является проблемой. Установи:

1. Какую реальную ответственность выполняет каждый symbol.
2. Кто его импортирует и вызывает.
3. Есть ли production consumer.
4. Есть ли tests.
5. Дублирует ли он другой слой.
6. Является ли abstraction полезной или speculative.
7. Нарушает ли он dependency direction.
8. Используется ли один и тот же термин одинаково в разных apps.

## Непереговорные правила аудита

### Не верить названию файла

`services.py` может содержать selectors, `repositories.py` — business logic, а `engine.py` — один helper. Классифицируй код по поведению, а не по имени.

### Не считать `rg` доказательством мёртвого кода

Django admin, signals, app configs, Celery autodiscovery, URL registration, aiogram routers, decorators и reflection могут использовать symbols динамически.

Перед пометкой `DEAD_CODE_CANDIDATE` проверь:

- прямые imports;
- re-exports;
- Django registration;
- admin registration;
- URL/router registration;
- Celery task names/autodiscovery;
- settings references;
- string-based model/task paths;
- tests;
- management commands;
- templates;
- migrations, если symbol исторический;
- runtime factories/registries.

Удаление возможно только в implementation mode и отдельным approved batch.

### Не создавать новую абстракцию без consumer

Protocol/interface/base class считается оправданным, только если существует реальная граница замены или несколько implementations/consumers. «Вдруг понадобится» не является основанием.

### Не нормализовать проект механически

Не создавай пустые `selectors.py`, `services.py`, `repositories.py` во всех apps ради одинакового дерева. Consistency означает одинаковое значение паттернов, а не одинаковое число файлов.

### Не путать архитектурный долг и дефект

Отдельно классифицируй:

- `BUG` — поведение неверно сейчас;
- `SECURITY/TENANCY_RISK` — возможна утечка или неавторизованное действие;
- `ARCHITECTURE_DEBT` — код работает, но границы размыты;
- `DEAD_CODE_CANDIDATE` — вероятно не используется, но требует безопасного удаления;
- `DUPLICATION` — одно правило реализовано в нескольких местах;
- `DOC_DRIFT` — документация не соответствует коду;
- `TEST_GAP` — важное поведение не защищено;
- `PERFORMANCE_RISK` — N+1, лишние запросы или blocking I/O;
- `PREMATURE_ABSTRACTION` — слой существует без обоснованной границы;
- `NAMING/PLACEMENT` — ответственность понятна, но находится/названа непоследовательно.

## Фаза 0. Безопасность и исходное состояние

Перед анализом:

1. Найди Git root и работай относительно него.
2. Прочитай все существующие `AGENTS.md`, `AGENTS.override.md`, `.codex/`, `.agents/`, configs и project instructions.
3. Выполни `git status --short`.
4. Зафиксируй существующие пользовательские изменения. Не перезаписывай и не форматируй их.
5. Определи Python/Django/aiogram/Celery versions из lock/config files.
6. Найди реальные команды test/lint/typecheck/format/migrations.
7. Не устанавливай новые dependencies без разрешения.
8. Не используй destructive Git commands.

Создай `docs/audits/BASELINE.md` со следующими данными:

- branch/commit, если Git доступен;
- dirty worktree summary;
- Python и dependency management;
- database modes;
- discovered processes;
- existing checks;
- результат baseline commands;
- отдельно: failures, существовавшие до изменений.

Попробуй выполнить документированные проверки, адаптировав только environment variables, если это безопасно:

```bash
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py check
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_USE_SQLITE=true ./.venv/bin/python manage.py test
./.venv/bin/ruff check .
```

Если `.venv` или команда отсутствует, найди фактический эквивалент. Не выдавай documented baseline `66 tests OK` за реальный результат текущего запуска.

## Фаза 1. Полная инвентаризация

Используй `rg --files`, Python AST tooling и безопасные read-only команды. Не dump-ь весь repository одним гигантским выводом — изучай системно по модулям.

Исключи из построчного анализа:

- `.git/`;
- `.venv/`;
- caches;
- generated static/build artifacts;
- third-party vendored code.

Но отдельно просмотри migrations как историю schema и constraints. Не оценивай каждую generated migration по style rules.

Создай `docs/audits/CODEBASE_INVENTORY.md`.

Для каждого app/package запиши:

| Поле | Содержание |
| --- | --- |
| Path | Путь модуля |
| Business responsibility | Что реально делает |
| Models | Основные persisted entities |
| Public entry points | Services/selectors/tasks/handlers/admin actions |
| Reads from | Зависимости чтения |
| Mutates | Какие aggregates изменяет |
| External I/O | Telegram/Celery/storage/network |
| Tenant boundary | Как определяется partner |
| Transactions | Где начинаются/заканчиваются |
| Tests | Какие behaviors защищены |
| Suspicions | Дублирование, dead code, inconsistent layer |

Обязательно проанализируй:

- все Django apps;
- `bot/handlers`, routers, middlewares, FSM, keyboards, runtime/context/content services;
- `core/` и settings;
- `infrastructure/`;
- management commands и seed logic;
- admin mixins/forms/actions;
- Celery tasks, routing и beat schedule;
- tests structure;
- pyproject/requirements/lock/config;
- Docker/compose/deployment configs;
- root documentation.

Создай `docs/audits/FILE_COVERAGE.md` или эквивалентный coverage ledger. Он должен доказать, что аудит не ограничился несколькими заметными файлами.

Минимальные статусы:

- `REVIEWED`;
- `GENERATED/HISTORICAL`;
- `EXCLUDED_WITH_REASON`;
- `NEEDS_FOLLOW_UP`.

## Фаза 2. Архитектура «как есть»

Создай `docs/architecture/AS_IS_ARCHITECTURE.md`.

Документ должен быть основан на коде и включать:

1. System context.
2. Runtime processes.
3. Django apps и реальные domain boundaries.
4. Основные user journeys.
5. Dependency direction между apps.
6. Tenant resolution/scoping.
7. Admin RBAC и staff-bot authorization.
8. Bot runtime и sync/async boundary.
9. Celery/notification delivery.
10. Основные state machines.
11. Database constraints и transaction boundaries.
12. Feature/capability resolution.
13. Где именно находятся business rules.
14. Mermaid diagrams только там, где они действительно помогают.

Построй import/dependency map. Если используешь скрипт, сохрани его только если он полезен для повторной проверки; иначе не засоряй repository одноразовым tooling.

Отдельно перечисли циклические или нежелательные зависимости:

```text
module A -> module B -> module A
bot handler -> ORM models from N apps
Celery task -> business logic directly
admin action -> duplicated domain transition
```

Не заявляй cycle без evidence.

## Фаза 3. Детальный architecture/code audit

Создай `docs/audits/ARCHITECTURE_AUDIT.md`.

Каждая находка должна иметь формат:

```text
ID: ARCH-XXX
Category: ARCHITECTURE_DEBT | BUG | SECURITY/TENANCY_RISK | ...
Severity: CRITICAL | HIGH | MEDIUM | LOW
Confidence: HIGH | MEDIUM | LOW
Evidence: exact paths + symbols + call sites/tests
Current behavior:
Why it matters:
Recommended target:
Migration/refactor risk:
Suggested batch:
Verification:
```

### 3.1. Layer consistency

Для каждого `services`, `selectors`, `repositories`, `interfaces`, `policies`, `rules`, `engine` составь таблицу:

| Symbol | Реальная ответственность | Consumers | Tests | Дублирует | Целевой слой | Решение |
| --- | --- | --- | --- | --- | --- | --- |

Возможные решения:

- `KEEP`;
- `RENAME`;
- `MOVE`;
- `MERGE`;
- `INLINE`;
- `SPLIT`;
- `DEPRECATE`;
- `DELETE_AFTER_PROOF`;
- `NEEDS_PRODUCT_DECISION`.

### 3.2. Business logic placement

Найди бизнес-правила в:

- handlers;
- admin actions;
- forms;
- model `save()`;
- signals;
- Celery tasks;
- repositories/selectors;
- duplicated helper functions.

Проверь, может ли один и тот же transition выполняться разными путями с разными проверками.

### 3.3. Tenant isolation

Проверь каждый tenant-owned read/write path:

- queryset scoping;
- FK/M2M choices в admin/forms;
- background tasks;
- Telegram callbacks/deep links;
- identifiers, получаемые от пользователя;
- cross-app services;
- reports/exports;
- global `TelegramAccount` + partner-scoped profiles;
- admin support/superuser behavior.

Особенно ищи `.objects.get(pk=...)`, `.filter(...)` и indirect lookups без подтверждённого partner context.

Не делай автоматический вывод по одному grep result: проследи call chain.

### 3.4. Transactions and concurrency

Проверь:

- где используется `transaction.atomic`;
- выполняется ли network I/O внутри transaction;
- корректно ли используется `transaction.on_commit`;
- race conditions при cart/session/order/bill/payment/bonus operations;
- unique/check constraints;
- `select_for_update` там, где возможна двойная обработка;
- idempotency Celery tasks и Telegram callbacks;
- partial failure между business state и notification scheduling.

Не навязывай outbox pattern автоматически. Если текущий `on_commit + Celery` недостаточен для требуемой гарантии, объясни конкретный failure window и предложи migration отдельно.

### 3.5. Django ORM and performance

Найди:

- N+1 в admin, bot screens и reports;
- repeated queries внутри loops;
- несогласованные repositories/selectors;
- materialization слишком больших QuerySets;
- отсутствие индексов под реальные tenant/status/date queries;
- SQLite behavior, которое может отличаться от PostgreSQL.

### 3.6. Async boundary

Проверь aiogram -> Django boundary:

- ORM calls без `sync_to_async`;
- слишком мелкие многократные `sync_to_async` calls;
- network I/O в sync services;
- shared mutable context между ботами;
- корректность token -> bot instance -> partner resolution;
- отсутствие доверия к `partner_id` из update/callback payload.

### 3.7. Permissions

Раздели и опиши:

- Django admin access;
- partner/tenant association;
- user role;
- employee profile;
- Telegram binding;
- notification preferences;
- staff-bot permissions.

Найди места, где одна система случайно считается доказательством другой.

### 3.8. State machines

Восстанови допустимые transitions минимум для:

- Partner;
- TableSession;
- Cart;
- Order;
- BillingRequest;
- Bill;
- Payment, если имеет lifecycle;
- BroadcastCampaign;
- StaffNotification;
- BonusTransaction/WalkInSale where applicable.

Сравни transitions в services, handlers, admin actions, models и tests.

### 3.9. Tests

Оцени не только количество tests, а защищённые invariants:

- tenant isolation;
- повторные `/start`/callbacks/tasks;
- concurrent cart/order/payment actions;
- state transitions;
- admin scoping;
- staff authorization;
- Celery retry/idempotency;
- feature flags/capabilities;
- Telegram content fallback;
- PostgreSQL-specific constraints.

### 3.10. Documentation quality

Проверь текущие документы на:

- абсолютные локальные пути вроде `/Users/...`;
- устаревшие test counts;
- смешение current state и future requirements;
- дублирующиеся разделы;
- нарушенную нумерацию;
- скрытые/commented требования;
- команды с demo credentials;
- противоречия в названиях модулей и статусах.

## Фаза 4. Целевая архитектура

На основании evidence предложи `docs/architecture/TARGET_ARCHITECTURE.md`.

Используй modular monolith. Не предлагай microservices без измеримой причины.

Базовый целевой словарь слоёв:

### Models

Persisted state, database constraints и локальные invariants одной entity. Не orchestration нескольких aggregates и не network I/O.

### Services

State-changing use cases, domain transitions и transaction boundaries. Services имеют конкретные business names, а не универсальный `BaseService`/CRUD.

### Selectors

Read-only application queries, tenant-scoped QuerySets/DTOs, `select_related/prefetch_related`. Никаких side effects.

### Repositories

Не являются обязательным слоем поверх Django ORM.

Repository оправдан только когда есть:

- реальная replaceable persistence/data-source boundary;
- внешний provider/storage/API;
- сложная инфраструктурная граница, которую domain/application code не должен знать;
- чёткий consumer и контракт.

Если repository лишь повторяет selector или `Model.objects`, предложи removal/merge, но только отдельным implementation batch с tests.

### Policies

Authorization, eligibility или capability decisions. Должны иметь ясные inputs/outputs и не мутировать состояние.

### Rules

Чистые domain calculations/invariants. Создавать отдельный модуль только если существует cohesive набор правил. Иначе располагать рядом с owning service/model/value object.

### Engine

Оставлять только для настоящего algorithm/orchestration component с ясным API, несколькими steps/strategies и тестами. Не использовать как название для случайного набора helpers.

### Interfaces

Protocols/ABCs только для реальных replaceable external boundaries или устойчивых cross-module contracts. Interface без production consumer или implementation — premature abstraction candidate.

### Celery tasks

Тонкие retryable wrappers: принимают primitive IDs, вызывают idempotent service/use case, классифицируют infrastructure errors. Не содержат дублированную бизнес-логику.

### Bot handlers

Transport/UI layer: parse update, validate interaction state, resolve actor/partner context, call selector/service, render response. Не управляют несколькими models напрямую.

### Admin/forms

Presentation, scoped choices, input validation и вызов safe domain action. Не создают альтернативную реализацию business transition.

Это начальная гипотеза. Если текущий проект требует отклонения, документируй его отдельным ADR с evidence.

## Фаза 5. Architecture Decision Records

Создай `docs/decisions/` и минимально необходимые ADR.

Предполагаемые решения, которые нужно подтвердить аудитом:

- `ADR-0001-layering-and-module-boundaries.md`;
- `ADR-0002-query-services-selectors-repositories.md`;
- `ADR-0003-tenant-context-and-scoping.md`;
- `ADR-0004-bot-django-async-boundary.md`;
- `ADR-0005-background-delivery-and-transactions.md`;
- `ADR-0006-feature-capability-resolution.md`.

Не создавай ADR ради каждого мелкого naming choice. Каждый ADR содержит:

- Status;
- Context;
- Decision;
- Alternatives considered;
- Consequences;
- Migration notes;
- Evidence paths.

## Фаза 6. Приведение документации в порядок

Не оставляй три гигантских root-файла как независимые конкурирующие источники истины.

Предложи и, если это безопасно в audit mode, создай следующую структуру:

```text
AGENTS.md
README.md
docs/
  product/
    PRODUCT_SPEC.md
    FEATURE_MATRIX.md
    OPEN_PRODUCT_QUESTIONS.md
  architecture/
    AS_IS_ARCHITECTURE.md
    TARGET_ARCHITECTURE.md
    MODULE_BOUNDARIES.md
    LAYERING_RULES.md
    TENANCY_AND_PERMISSIONS.md
    BOT_RUNTIME.md
    BACKGROUND_JOBS.md
  engineering/
    DEVELOPMENT.md
    TESTING.md
    CODE_STYLE.md
  audits/
    BASELINE.md
    CODEBASE_INVENTORY.md
    FILE_COVERAGE.md
    ARCHITECTURE_AUDIT.md
    REFACTOR_ROADMAP.md
  decisions/
    ADR-....md
.agents/
  skills/
    tableos-development/
      SKILL.md
      agents/
        openai.yaml
```

Это целевой ориентир, не требование создать пустой файл для каждого названия. Объединяй документы, если два файла будут дублировать друг друга.

Правила единственного источника истины:

- `README.md` — короткий onboarding: что это, как запустить, основные ссылки;
- `docs/product/` — что продукт должен делать;
- `docs/architecture/AS_IS_ARCHITECTURE.md` — как он реально устроен сейчас;
- `docs/architecture/TARGET_ARCHITECTURE.md` и ADR — как должны строиться новые изменения;
- `docs/engineering/` — команды и coding rules;
- `docs/audits/` — findings и временный migration roadmap;
- `AGENTS.md` — короткие обязательные правила и маршрутизация к подробным документам;
- skill — workflow агента, а не вторая копия всего ТЗ.

При переносе существующего `TABLEOS_TZ` не потеряй продуктовые требования. Составь migration map `old section -> new canonical document`. Сначала создай и проверь новые документы; удаление/переименование старых файлов предложи отдельно, если оно может сломать ссылки.

Исправь абсолютные локальные ссылки на repository-relative paths.

Не храни реальные tokens, secret keys или production credentials в документации.

## Фаза 7. Создание `AGENTS.md`

Создай корневой `AGENTS.md`, который автоматически направляет будущих agents.

Он должен быть коротким и практичным, не копировать ТЗ целиком.

Обязательные разделы:

1. Product/system summary.
2. Source-of-truth document map.
3. Mandatory workflow before edits.
4. Fixed architecture/layer vocabulary.
5. Tenant and permission invariants.
6. Django/aiogram/Celery boundaries.
7. Money/order/payment/bonus integrity rules.
8. Test/lint/migration commands, подтверждённые реальным repository.
9. Definition of Done.
10. Prohibited patterns.
11. Rule that architecture changes require ADR/update of target docs.

Примеры обязательных запретов, если аудит их подтверждает:

- не добавлять новый layer/module pattern только в одном app без ADR;
- не создавать repository, который просто оборачивает ORM;
- не помещать business transition в handler/admin/task;
- не выполнять Telegram/network I/O в DB transaction;
- не принимать `partner_id` от update/request как authority;
- не добавлять shared abstraction до появления реального повторного consumer;
- не удалять allegedly unused code без dynamic registration checks;
- не менять применённые migrations;
- не использовать `float` для денег;
- не оставлять implementation без tests и обновления docs при изменении контракта.

Учитывай default combined instruction limit. Подробности должны оставаться в `docs/`, а `AGENTS.md` — ссылаться на них.

## Фаза 8. Repository-scoped skill

Создай один основной repository skill:

```text
.agents/skills/tableos-development/SKILL.md
```

Если доступен `$skill-creator`, используй его правила формата и validation, но skill должен остаться repository-scoped. Не устанавливай personal/global skill без запроса владельца.

Skill name:

```yaml
name: tableos-development
```

Description должна явно trigger-иться для:

- implementation;
- refactoring;
- debugging;
- review;
- architecture changes;
- Django apps;
- aiogram flows;
- Celery jobs;
- tenant scoping;
- orders/billing/bonuses/notifications.

Skill должен:

1. Сначала читать корневой `AGENTS.md`.
2. Определять затронутые product flows и modules.
3. Загружать только релевантные canonical docs.
4. Проверять tenant/permission boundary.
5. Проверять state transitions, transactions, idempotency и async boundary.
6. Запрещать новый architecture pattern без ADR.
7. Требовать tests и exact verification commands.
8. Требовать отчёт о changed contracts, migrations, risks и checks.

Не копируй все документы внутрь skill. Skill маршрутизирует агента к canonical `docs/`.

Не создавай много skills ради количества. Дополнительный skill допустим только если аудит выявил отдельный повторяемый workflow с собственными triggers, например сложный release verification. Сначала обоснуй его.

## Фаза 9. Refactoring roadmap

Создай `docs/audits/REFACTOR_ROADMAP.md`.

Roadmap должен состоять из небольших reversible batches, а не «переписать архитектуру».

Для каждого batch:

```text
Batch ID:
Goal:
Problem/findings addressed:
Exact scope/files:
Behavior that must remain unchanged:
Target rule/ADR:
Tests to add before changes:
Implementation steps:
Migration/import compatibility:
Verification commands:
Rollback strategy:
Dependencies on other batches:
Risk:
Estimated size: S | M | L
```

Приоритет:

1. Critical tenant/security/correctness risks.
2. Test characterization вокруг опасных flows.
3. Устранение дублированных business transitions.
4. Clarification of service/selector/repository/policy boundaries.
5. Safe dead-code removal.
6. Package/file moves.
7. Naming/style cleanup.

Не начинай с массового перемещения файлов: сначала characterization tests и public import compatibility.

Если module relocation нужен:

- оцени external/internal imports;
- временно оставь compatibility re-export, если это уменьшает риск;
- убери re-export отдельным later batch;
- не смешивай relocation с изменением behavior.

## Фаза 10. Финальный отчёт audit mode

Создай `docs/audits/AUDIT_SUMMARY.md` и выдай пользователю компактный итог.

В итог включи:

- сколько apps/modules/files реально проверено;
- baseline checks и результаты;
- current architecture summary;
- 10–20 главных findings по severity;
- особенно: selectors/services/repositories/interfaces/policies/rules/engine findings;
- какие документы созданы/реорганизованы;
- какой `AGENTS.md` создан;
- какой repo skill создан и как вызвать `$tableos-development`;
- proposed target architecture;
- список roadmap batches в рекомендуемом порядке;
- какие вопросы требуют решения владельца;
- какие изменения application code намеренно не выполнялись.

После этого остановись фразой по смыслу:

```text
Audit and governance phase is complete. No application-code refactoring was performed.
Choose/approve the first roadmap batch before implementation.
```

## Режим реализации после подтверждения

Только после явного сообщения владельца вида:

```text
MODE = IMPLEMENT_APPROVED_BATCH
APPROVED_BATCH = R-XXX
```

можно менять application code.

Для implementation mode:

1. Перечитай `AGENTS.md`, соответствующие canonical docs, ADR и approved batch.
2. Проверь `git status`; сохрани unrelated user changes.
3. Подтверди exact scope и invariants.
4. Сначала добавь characterization/regression tests.
5. Запусти tests и докажи, что они защищают поведение.
6. Выполни только один approved batch.
7. Не смешивай behavior change, file relocation и broad formatting, если batch этого явно не требует.
8. Обнови imports/docs/ADR/skill только в пределах batch.
9. Запусти focused tests, затем полный documented suite.
10. Сравни migrations; не допускай случайной schema change.
11. Проверь tenant isolation и async/transaction boundaries.
12. Выдай diff summary и остановись перед следующим batch.

Если во время реализации обнаружена более серьёзная проблема:

- не расширяй scope молча;
- зафиксируй новый finding;
- предложи новый/изменённый batch;
- остановись, если проблема меняет approved plan.

## Стандарт доказательности

Не использовать формулировки:

- «кажется, не используется» без call-site/dynamic registration checks;
- «архитектура чистая» без module/dependency map;
- «все тесты проходят» без exact command/result;
- «tenant-safe» без negative permission paths;
- «рефакторинг без изменения поведения» без characterization tests;
- «весь проект проверен» без coverage ledger.

Каждый важный вывод должен опираться минимум на одно из:

- path + symbol;
- call chain;
- model/DB constraint;
- test;
- runtime registration;
- command output;
- documented product requirement с явной пометкой, что это target, а не current behavior.

## Ожидаемый результат

После первого запуска проект должен получить не массовую перестройку кода, а надёжную основу для неё:

- доказанную карту текущей системы;
- список реальных проблем вместо предположений;
- единый словарь layers;
- target architecture и ADR;
- очищенную структуру документации;
- корневой `AGENTS.md`;
- repository-level `$tableos-development` skill;
- детальный безопасный roadmap;
- baseline, позволяющий видеть, когда дальнейший рефакторинг что-то сломал.

Главный критерий успеха: следующий coding-agent не выбирает архитектурный стиль заново в каждой задаче, а может объяснить, куда относится новый код, почему он находится именно там и какими tests/invariants это решение защищено.

