# TableOS — каноническая продуктовая спецификация

**Статус:** canonical product contract for future approved work.  
**Последняя сверка с кодом:** audit-and-governance, 2026-07-27; post-B3 review at `ddb7dce`, 2026-10-23.\
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

В текущий product scope **не входят как готовые journeys** public REST API, WebApp, production webhook runtime, POS/fiscal providers, online payments и delivery. Pickup реализован с предоплатой переводом ([PICKUP.md](../architecture/PICKUP.md)). Наличие model fields или flags не означает, что journey существует.

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
| Tables and QR sessions | Stable QR payload, many guests per table, one active session per guest/partner | Tables | PARTIAL_CURRENT — B2 закрепил одну активную сессию (partial unique constraint, PostgreSQL-тест); expiry не реализован (PQ-008) |
| Cart and table ordering | Guest-owned cart, order snapshots, staff notification | Menu + orders; table journey requires tables | CONFIRMED_CURRENT for main journey; integrity work remains |
| Billing | Personal/shared bill, custom split request, partial/full payment, partner-defined bonus payment with a monetary remainder | Orders + loyalty | Scoped to the table visit since B3.2 part B (AA-020); payment rollback (AA-021) and PostgreSQL bill creation (AA-033) fixed in B3.1; bill changes/void (AA-022) in B3.2 part A |
| Staff call | Role-directed notification from active table | Tables | CONFIRMED_CURRENT, cooldown is check-then-create and not race-safe |
| Bonus programmes | Visit, manual purchase, and extensible partner-defined reward triggers; auditable ledger | Guest profile + billing settlement | PARTIAL_CURRENT; paid settlement is the current order-reward baseline; cashback accrues on the full total or only on money paid, per programme (PQ-030, B3.2 part C); programme trigger expansion remains future work |
| Quick sale | Staff sale with optional customer code and bonus redemption | Menu; staff capability | CONFIRMED_CURRENT |
| Staff operations | Orders, notifications, tables, billing, quick sale, reports by capability | Employee + Telegram binding | PARTIAL_CURRENT — B1 rechecks active user/profile/partner and capability on every callback; formal role matrix is PQ-004 |
| Daily report | Operational day summary, tails, bills/quick sales | Billing and/or quick sale | CONFIRMED_CURRENT as on-demand MVP |
| Broadcasts | Partner-scoped marketing respecting preferences; scheduled delivery | Celery + Telegram | CONFIRMED_CURRENT since B4: at most once per guest and campaign (PQ-014, ADR-0008) |
| Pickup | Prepaid by transfer (with optional bonuses), ASAP / 30 / 60 min within opening hours, phone required, staff confirms the transfer | Menu + orders + cart; billing used internally | CONFIRMED_CURRENT ([PICKUP.md](../architecture/PICKUP.md)) |
| Delivery / shipping | Separate journeys, not merely flags | Product design required | CODE_IS_BEHIND_SPEC / future scope |
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
   - If the table already has an active visit of other guests, the bot asks whether to **join** that visit or **start a new one** (ADR-0007, approved 2026-10-23). Starting a new visit leaves the previous visit open and flags the table to staff, who close it manually.
3. A new session closes that guest’s prior active session in the same partner only after defined settlement rules are considered.
4. Guest builds a personal cart; price/name snapshots are written to order items at checkout.
5. Staff receives a durable operational notification; staff accepts, prepares, marks ready/completed through the allowed state machine.
6. Guest can view only their active-session orders and may confirm receipt from any non-final status, including `new`; the confirmation completes the order and is recorded as a guest action (PQ-029).

Several guests may use one table. Their carts and orders remain personal; a shared bill aggregates only orders of the same visit.

### 5.4 Billing and payment

1. Guest asks for personal, shared, or custom-split settlement.
2. Personal/shared request prepares or extends an eligible draft bill; custom split creates a request for staff handling.
3. A bill contains only same-partner eligible order items of one visit (personal: one guest within the visit).
4. Staff issues the bill and may choose “pay with bonuses”; the command applies the partner-allowed amount at 1 bonus = 1 UAH and leaves any monetary remainder on the bill.
5. Staff records the remaining cash/terminal payment, or the bonus command closes a fully covered bill with the normal `bonuses` payment method; if the partner enables auto-close, only sessions whose orders are all paid are closed, and auto-close never blocks the payment (PQ-027).
6. Staff with a bill-change permission granted by the venue owner may change a bill (remove/add lines, including a cancelled order's lines) or void it. Each change recalculates the bill and is kept in an audit history with actor, time and reason. A change may not reduce the total below the amount already paid; voiding releases the orders for re-billing (PQ-028).

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
| TableSession | `active`, `closed`, `timed_out`; exactly one active session per guest/partner, many active guests per table; every session belongs to one table visit (ADR-0007) |
| Cart | `active -> checked_out` or `abandoned`; stale cart must not silently migrate to a new table session |
| Order | `new -> accepted -> preparing? -> ready -> delivering? -> completed`, with cancellation only through allowed paths; `completed` does not necessarily mean paid |
| Bill | `draft -> issued -> partially_paid -> paid` or `canceled` (void, owner-granted permission, releases allocations); a paid/canceled bill cannot accept an ordinary payment; line changes are audited and never drop the total below the paid amount |
| BillingRequest | `open`, `auto_prepared`, `processed`, `canceled`; only legal service transitions |
| BonusTransaction | immutable ledger rows for accrual, redemption, expiration and manual adjustment |
| BroadcastCampaign | `draft -> scheduled -> sending -> sent/failed`; per-guest `BroadcastDelivery` `pending -> sending -> sent/failed/skipped/unknown`, never resent (ADR-0008) |

## 7. Content and configuration contract

Partners configure approved labels, templates and capability flags through `PartnerBotSettings`. Empty configuration falls back to safe default text; rendering errors must not crash update handling. The product requires stable intent routing even when display labels collide or are renamed. Therefore labels are presentation data, not an authority or an unvalidated routing key.

The desired configuration UX includes dependency validation, human-readable preview and a documented set of supported template variables. It does not require a separate code branch per partner.

## 8. Confirmed divergence register

The following are not silently “normalised” in this product document; implementation requires approved roadmap work.

| Product requirement | Code evidence | Classification | Consequence / target |
| --- | --- | --- | --- |
| Inactive `User` must not retain staff access | `TABLEOS_TZ.md:284-295`; B1: `apps/employees/selectors.py:get_staff_employee_by_telegram` requires active profile, active user and matching partner; every staff callback re-resolves the actor | RESOLVED_IN_B1 | Keep the characterization tests green |
| Suspended partner must not serve ongoing flows | `TABLEOS_TZ.md:1880-1892`; B1: `bot/middlewares/partner_runtime.py:PartnerRuntimeMiddleware` and `bot/services/context.py:resolve_partner_for_bot_token` recheck partner and bot status on every update | RESOLVED_IN_B1 | Polling transport stops only on process restart (PQ-013) |
| One active session per guest/partner | `TABLEOS_TZ.md:1900-1907`; B2: `TableSession.unique_active_table_session_per_partner_guest`; `apps/tables/services.py:activate_table_session` locks `GuestProfile → Table → active sessions` | RESOLVED_IN_B2 | Run the duplicate audit and the PostgreSQL test before production migration |
| A bill item cannot appear in two active/paid bills | `apps/billing/services.py:create_bill_from_orders` and `attach_orders_to_bill` lock `OrderItem` rows; `BillItem.unique_order_item_bill_allocation` guards `(partner, order_item)` | PARTIALLY_RESOLVED_FOR_B3 | Repeat the duplicate audit; a PostgreSQL allocation test still has to be written; bill void/release is PQ-028 (AA-022) |
| Order status transition is serialized | `TABLEOS_TZ.md:531-691`; B2: `apps/orders/services.py:transition_order_status` reloads the order under `select_for_update()`; guest confirmation still bypasses the transition table (AA-025) | RESOLVED_IN_B2; guest rule RESOLVED_IN_B3.1 | Guest may confirm from any non-final status (PQ-029) |
| Order reward programmes accrue | `apps/billing/services.py:_sync_orders_after_bill_paid` dispatches the existing programme event after first paid settlement with source-linked idempotency | PARTIALLY_RESOLVED_FOR_B3 | Paid is the current baseline; partner-specific trigger expansion and refund/cancellation treatment remain deferred |
| Labels cannot misroute guest actions | `TABLEOS_TZ.md:1512-1537`; `bot/filters/content.py:PartnerButtonFilter` compares raw message text; B1: `apps/partners/models.py:PartnerBotSettings.clean` rejects empty and duplicate routed labels | RESOLVED_IN_B1 (validation) | Stable intent routing remains a future choice (PQ-016) |
| Telegram I/O is outside transaction and broadcasts are retry-safe | `TABLEOS_TZ.md:1852-1877`; B4: `apps/notifications/services.py:send_broadcast_campaign_now` claims `BroadcastDelivery` rows, sends with no transaction open and records each outcome; `recover_stalled_broadcast_campaigns` | RESOLVED_IN_B4 (AA-008, AA-030) | At most once for broadcasts, at least once for operational notifications (PQ-014, PQ-015) |
| Admin cannot bypass operational transition rules | B2/B3 make lifecycle, allocation, bonus balance and ledger fields readonly; payment/bonus commands remain owner services and no special bonus-settlement Admin action exists | PARTIALLY_RESOLVED_FOR_B3 | Correction policy (PQ-018), order relations read-only since B3.1, PostgreSQL rollout gate remain open |
| Delivery/pickup are usable journeys | Pickup: `apps/billing/remote_orders.py`, `bot/handlers/remote_checkout.py` (one flow for every remote channel, ADR-0009); delivery has only its flag | Pickup RESOLVED; delivery CODE_IS_BEHIND_SPEC | Delivery and shipping are the next channels (ECOSYSTEM_DIRECTION.md) |
| A bill contains only the current visit's orders | B3.2 part B: `apps/billing/table_bills.py:get_table_billable_orders` filters by `TableSession.visit`; `create_bill_from_orders` rejects orders of different visits; `bot/services/navigation.py` checks the guest's visit | RESOLVED_IN_B3.2B (AA-020) | Guest chooses to join a party or start a new visit (PQ-026); staff close leftover visits |
| Recording a payment does not fail because of follow-up steps | Before B3.1 the table-wide auto-close raised `TableSessionError` inside the payment transaction; now `apps/billing/table_bills.py:_close_settled_sessions_of_bill` skips non-closable sessions | RESOLVED_IN_B3.1 (AA-021) | Payment always persists; auto-close closes only fully paid sessions (PQ-027) |
| A guest is not charged for a cancelled order | `apps/orders/services.py:transition_order_status` cancels allocated orders; `recalculate_bill_totals` keeps their lines; no bill void command exists | RESOLVED_IN_B3.2A (AA-022) | Owner-granted, audited bill change/void; void releases allocations (PQ-028) |
| Messages render any venue/guest text | All messages use HTML parse mode; no dynamic value is escaped | RESOLVED_IN_B3.1 (AA-024) | Broadcast text remains partner markup until B4 |

## 9. Future scope and exclusions

The following need their own approved product/design batch before implementation: delivery/shipping data model and UX, WebApp, webhook runtime, online payment provider callbacks, POS/fiscal integrations, refund lifecycle, loyalty expiry automation, segmentation/preview/unsubscribe for broadcasts, export/dashboard UX, multilingual policy, inventory/stop-lists, service charge/tips and table-session expiry policy.

Open choices that affect behavior are maintained in [OPEN_PRODUCT_QUESTIONS.md](OPEN_PRODUCT_QUESTIONS.md); they are not resolved by this audit.
