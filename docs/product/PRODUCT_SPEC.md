# TableOS — каноническая продуктовая спецификация

**Статус:** canonical product contract for future approved work.  
**Последняя сверка с кодом:** audit-and-governance, 2026-07-27.  
**Не является описанием текущего поведения:** реальное состояние зафиксировано в [AS_IS_ARCHITECTURE.md](../architecture/AS_IS_ARCHITECTURE.md), а расхождения — в [FEATURE_MATRIX.md](FEATURE_MATRIX.md) и [ARCHITECTURE_AUDIT.md](../audits/ARCHITECTURE_AUDIT.md).

## Как читать документ

`TABLEOS_TZ.md` сохранён как полный legacy source и evidence желаемого поведения. Его разделы сопоставлены с этим набором документов в [LEGACY_TZ_MIGRATION_MAP.md](LEGACY_TZ_MIGRATION_MAP.md). Этот документ не объявляет желаемое поведение уже реализованным.

Используются четыре статуса сверки:

- **CONFIRMED_CURRENT** — поведение подтверждено кодом и входит в нынешний MVP;
- **CODE_IS_BEHIND_SPEC** — продуктовый контракт принят как целевой, но код его не выполняет полностью;
- **AMBIGUOUS_PRODUCT_DECISION** — нужен выбор владельца до изменения поведения;
- **NEEDS_FOLLOW_UP** — утверждение требует отдельной проверки, измерения или design decision.

## 1. Назначение и границы продукта

TableOS — multi-tenant white-label B2B платформа для заведений. Одна Django/aiogram codebase обслуживает несколько партнёров и их Telegram-ботов. Партнёр получает свой контент, меню, столы, сотрудников, операционную историю, лояльность и включённые capabilities; код не должен превращаться в отдельную версию продукта для каждого заведения.

Базовая ценность продукта:

1. зарегистрировать гостя в контексте конкретного заведения и выдать код клиента;
2. дать заведению настраиваемый Telegram UX;
3. подключать меню, столы, заказы, счета, быстрые продажи, отчёты и рассылки как capabilities;
4. сохранять проверяемый след по заказам, оплатам, бонусам и уведомлениям;
5. сохранять строгую изоляцию данных и действий партнёров.

В текущий product scope **не входят как готовые journeys** public REST API, WebApp, production webhook runtime, POS/fiscal providers, online payments, delivery и pickup. Наличие model fields или flags не означает, что journey существует.

## 2. Роли и контексты доступа

| Роль/контекст | Продуктовое назначение | Граница полномочий |
| --- | --- | --- |
| Platform administrator | Управляет всей платформой, партнёрами, ботами и чувствительными настройками | Может пересекать tenant boundary только через явный platform access |
| Partner owner | Владелец данных и конфигурации одного заведения | Только свой `partner` |
| Partner manager | Операционное управление в рамках разрешённых секций | Только свой `partner` и выделенные admin sections |
| Cashier | Счета, оплаты и быстрые продажи | Не получает права owner/manager автоматически |
| Waiter / hookah master | Операционные staff actions и notifications | Доступ определяется employee profile, Telegram binding и role/capability |
| Guest | Профиль лояльности, guest journeys | Один глобальный Telegram identity может иметь отдельные guest profiles у разных partners |

**Обязательное разделение:** Django-admin RBAC, `User.role`, `EmployeeProfile`, Telegram binding и notification preferences — самостоятельные доказательства доступа. Ни одна из этих сущностей сама по себе не должна автоматически доказывать другую.

## 3. Непереговорные продуктовые инварианты

1. **Tenant isolation.** Tenant-owned entity, read, callback, background job и related-object choice должны быть ограничены тем же `partner`. Глобальный `TelegramAccount` допустим только как identity; guest/employee/admin context остаётся partner-scoped.
2. **Активность и доступ.** Suspended partner не обслуживает реальные guest/staff flows. Staff action требует активных `EmployeeProfile` **и** `User`, явной Telegram binding и разрешённой capability.
3. **Auditability.** Изменение заказа, счёта, оплаты, бонусного баланса и уведомления оставляет persisted trace. Админский UI не является обходом domain transition.
4. **Money integrity.** Денежные суммы — decimal values. Один order item не может быть распределён в два открытых/оплаченных счёта; платежи, bonus redemption и bill state должны быть идемпотентны и конкурентно безопасны.
5. **State integrity.** Все state transitions проходят через единственный безопасный use case с разрешёнными переходами, actor/capability checks и транзакционной защитой.
6. **Delivery boundary.** Бизнес-событие не откатывается потому, что Telegram delivery не удалась. Network I/O не выполняется внутри DB transaction; delivery имеет наблюдаемый result и определённую семантику retry/idempotency.
7. **Capability-driven UX.** Включённые capability и текущий journey определяют, что видит пользователь. Выключенная capability даёт понятный fallback и не раскрывает недоступное действие.

## 4. Capability model

| Capability | Целевой контракт | Зависимости | Статус сверки |
| --- | --- | --- | --- |
| Guest profile and loyalty identity | `/start`, partner-scoped `GuestProfile`, customer code, profile | Always available | CONFIRMED_CURRENT, с отдельными bonus gaps |
| Menu browsing | Active categories/items, configurable label, optional browse without table | Menu | CONFIRMED_CURRENT |
| Tables and QR sessions | Stable QR payload, many guests per table, one active session per guest/partner | Tables | CODE_IS_BEHIND_SPEC — concurrency invariant не закреплён |
| Cart and table ordering | Guest-owned cart, order snapshots, staff notification | Menu + orders; table journey requires tables | CONFIRMED_CURRENT for main journey; integrity work remains |
| Billing | Personal/shared bill, custom split request, partial/full payment | Orders | CONFIRMED_CURRENT for MVP; allocation integrity gap |
| Staff call | Role-directed notification from active table | Tables | CONFIRMED_CURRENT, cooldown race requires fix |
| Bonus programmes | Visit, manual purchase, order-completed reward; auditable ledger | Guest profile | CODE_IS_BEHIND_SPEC for order-completed trigger |
| Quick sale | Staff sale with optional customer code and bonus redemption | Menu; staff capability | CONFIRMED_CURRENT |
| Staff operations | Orders, notifications, tables, billing, quick sale, reports by capability | Employee + Telegram binding | CODE_IS_BEHIND_SPEC for inactive user revocation and stale capability callbacks |
| Daily report | Operational day summary, tails, bills/quick sales | Billing and/or quick sale | CONFIRMED_CURRENT as on-demand MVP |
| Broadcasts | Partner-scoped marketing respecting preferences; scheduled delivery | Celery + Telegram | CODE_IS_BEHIND_SPEC for delivery transaction/idempotency semantics |
| Delivery / pickup | Separate journeys, not merely flags | Product design required | CODE_IS_BEHIND_SPEC / future scope |
| Webhook runtime | Explicit production runtime with observability | Product/ops decision | CODE_IS_BEHIND_SPEC / future scope |

### Capability dependencies

- Cart requires menu and orders.
- Billing requires orders.
- Table ordering and staff call require tables.
- Quick sale requires menu.
- Reports require a defined source: billing and/or quick sale.
- Delivery and pickup, once approved, are **parallel journeys**, not mutually exclusive global “order mode” switches.

## 5. Core user journeys

### 5.1 Loyalty-only guest

1. Guest opens `/start` for a partner bot.
2. System resolves partner from bot runtime, creates or updates a partner-scoped guest profile, and shows customer code/profile entry point.
3. Cashier/staff may record an auditable manual purchase or approved bonus operation.
4. Guest sees balance and transaction history without being forced into table/order UX.

### 5.2 Menu-only guest

1. Guest starts bot and opens menu.
2. Only active categories and available items are shown.
3. If ordering requires a table session, the UI presents a clear hint and no add-to-cart action.
4. A menu-only partner must not be told to scan a table QR.

### 5.3 Table order

1. Guest scans `table_<number>_<token>` QR deep link.
2. System validates partner, table, token and table availability; it resolves/creates the guest profile.
3. A new session closes that guest’s prior active session in the same partner only after defined settlement rules are considered.
4. Guest builds a personal cart; price/name snapshots are written to order items at checkout.
5. Staff receives a durable operational notification; staff accepts, prepares, marks ready/completed through the allowed state machine.
6. Guest can view only their active-session orders and may confirm receipt only when transition rules allow it.

Several guests may use one table. Their carts and orders remain personal; shared bill aggregates eligible orders only after explicit selection.

### 5.4 Billing and payment

1. Guest asks for personal, shared, or custom-split settlement.
2. Personal/shared request prepares or extends an eligible draft bill; custom split creates a request for staff handling.
3. A bill contains only same-partner eligible order items in a compatible table/guest context.
4. Staff issues the bill, records valid payment(s), optionally redeems bonus balance for a personal bill, and sees remaining amount.
5. Full payment updates related orders and requests through the same use case; table session closes only when settlement and receipt conditions are clean.

### 5.5 Staff operations

1. `/staff` resolves partner from bot token, then employee from the explicit partner-scoped binding.
2. Each screen/action rechecks active identity, role and enabled capability; a stale button cannot retain an authority that was revoked.
3. Staff actions use named domain services, not direct model edits.
4. A staff member may see exactly the operational records allowed for their partner and role.

### 5.6 Broadcasts and notifications

1. A domain service records the business event and schedules delivery only after commit.
2. A task invokes an idempotent delivery use case using primitive identifiers.
3. Delivery result is visible; retry behavior cannot silently double-send a campaign batch or hold a DB transaction during Telegram I/O.
4. Marketing recipient selection respects partner, subscription/preference and blocked-account state at send time.

## 6. State contracts

| Aggregate | Expected states / rule |
| --- | --- |
| Partner | `draft -> active -> suspended`; a suspended partner must not serve traffic or delivery |
| TableSession | `active`, `closed`, `timed_out`; exactly one active session per guest/partner, many active guests per table |
| Cart | `active -> checked_out` or `abandoned`; stale cart must not silently migrate to a new table session |
| Order | `new -> accepted -> preparing? -> ready -> delivering? -> completed`, with cancellation only through allowed paths; `completed` does not necessarily mean paid |
| Bill | `draft -> issued -> partially_paid -> paid` or `canceled`; a paid/canceled bill cannot accept an ordinary payment |
| BillingRequest | `open`, `auto_prepared`, `processed`, `canceled`; only legal service transitions |
| BonusTransaction | immutable ledger rows for accrual, redemption, expiration and manual adjustment |
| BroadcastCampaign | explicit queued/sending/sent/failed behavior with a retry/idempotency contract |

## 7. Content and configuration contract

Partners configure approved labels, templates and capability flags through `PartnerBotSettings`. Empty configuration falls back to safe default text; rendering errors must not crash update handling. The product requires stable intent routing even when display labels collide or are renamed. Therefore labels are presentation data, not an authority or an unvalidated routing key.

The desired configuration UX includes dependency validation, human-readable preview and a documented set of supported template variables. It does not require a separate code branch per partner.

## 8. Confirmed divergence register

The following are not silently “normalised” in this product document; implementation requires approved roadmap work.

| Product requirement | Code evidence | Classification | Consequence / target |
| --- | --- | --- | --- |
| Inactive `User` must not retain staff access | `TABLEOS_TZ.md:284-295`; `apps/employees/selectors.py:get_staff_employee_by_telegram` filters `EmployeeProfile.is_active` only; `bot/handlers/staff.py:_resolve_staff_employee_by_telegram_id` uses it | CODE_IS_BEHIND_SPEC | Revoke access when either profile or user is inactive; characterize `/staff` and callback paths first |
| Suspended partner must not serve ongoing flows | `TABLEOS_TZ.md:1880-1892`; `bot/services/runtime.py:load_active_bot_configs` checks status only at startup; `bot/services/context.py:resolve_partner_for_bot_token` returns partner without status gate | CODE_IS_BEHIND_SPEC | Decide and implement runtime-time suspension behavior |
| One active session per guest/partner | `TABLEOS_TZ.md:1900-1907`; `apps/tables/models.py:TableSession.Meta` has no uniqueness constraint; `apps/tables/services.py:activate_table_session` locks `Table`, then creates session | CODE_IS_BEHIND_SPEC | Add a PostgreSQL-safe concurrency design after characterization tests |
| A bill item cannot appear in two active/paid bills | `TABLEOS_TZ.md:763-947`; `apps/billing/services.py:create_bill_from_orders` checks then bulk-creates without locking order items or DB uniqueness | DEFECT / CODE_IS_BEHIND_SPEC | Define allocation invariant and transactional enforcement before financial changes |
| Order status transition is serialized | `TABLEOS_TZ.md:531-691`; `apps/orders/services.py:transition_order_status` mutates caller-provided order without fresh `select_for_update()` | DEFECT | Serialize callback transitions and prevent duplicate history/notifications |
| Order-completed programmes accrue | `TABLEOS_TZ.md:1012-1097`; `apps/bonuses/services.py:apply_bonus_programs` supports event, but only table activation invokes it for `VISIT`; no production order completion call is present | CODE_IS_BEHIND_SPEC | Owner must choose completed vs paid vs both, then add one controlled trigger |
| Labels cannot misroute guest actions | `TABLEOS_TZ.md:1512-1537`; `bot/filters/content.py:PartnerButtonFilter` compares raw message text; `apps/partners/models.py:PartnerBotSettings.clean` validates dependencies but not label uniqueness | DEFECT / CODE_IS_BEHIND_SPEC | Validate collisions or route with stable command/intent identifiers |
| Telegram I/O is outside transaction and broadcasts are retry-safe | `TABLEOS_TZ.md:1852-1877`; `apps/notifications/services.py:send_broadcast_campaign_now` is `@transaction.atomic` and calls `_send_telegram_messages` at line 503; `apps/notifications/tasks.py:send_broadcast_campaign_task` has no durable lease/idempotency cursor | DEFECT | Separate claim/send/finalize phases; decide delivery guarantee explicitly |
| Admin cannot bypass operational transition rules | `TABLEOS_TZ.md:1752-1808`; actual admins expose mutable order/bill/session fields and `apps/billing/admin.py` uses direct `queryset.update` actions | CODE_IS_BEHIND_SPEC | Restrict direct editing and route mutations through safe services |
| Delivery/pickup are usable journeys | `TABLEOS_TZ.md:2220-2245`; `PartnerBotSettings.clean` accepts delivery/pickup flags at `apps/partners/models.py:264-272`, but there is no established delivery/pickup order journey | CODE_IS_BEHIND_SPEC | Keep flags as capability groundwork; do not market as implemented flows |

## 9. Future scope and exclusions

The following need their own approved product/design batch before implementation: delivery/pickup data model and UX, WebApp, webhook runtime, online payment provider callbacks, POS/fiscal integrations, refund lifecycle, loyalty expiry automation, segmentation/preview/unsubscribe for broadcasts, export/dashboard UX, multilingual policy, inventory/stop-lists, service charge/tips and table-session expiry policy.

Open choices that affect behavior are maintained in [OPEN_PRODUCT_QUESTIONS.md](OPEN_PRODUCT_QUESTIONS.md); they are not resolved by this audit.
