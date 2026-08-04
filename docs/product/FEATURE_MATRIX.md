# TableOS — feature and implementation matrix

**Purpose:** one compact view of desired product capabilities against confirmed current behavior. It does not replace the full product contract in [PRODUCT_SPEC.md](PRODUCT_SPEC.md) or the code-level findings in [ARCHITECTURE_AUDIT.md](../audits/ARCHITECTURE_AUDIT.md).

## Status legend

| Status | Meaning |
| --- | --- |
| CONFIRMED_CURRENT | Main behavior is confirmed by source and is part of the current MVP |
| PARTIAL_CURRENT | Some path exists, but important product conditions remain unproven or missing |
| CODE_IS_BEHIND_SPEC | A desired behavior is documented but not fully implemented |
| DEFECT | Current implementation can violate an already-stated product invariant |
| AMBIGUOUS_PRODUCT_DECISION | Owner choice is needed before defining the implementation contract |
| FUTURE_SCOPE | Explicitly not offered as a current journey |

## Capability matrix

| Capability | Desired product contract | Confirmed implementation evidence | Status | Gap / decision needed |
| --- | --- | --- | --- | --- |
| Multi-tenant domain data | All partner-owned reads/writes/actions use the same partner context | `PartnerBoundModel`; scoped bot and admin entry points; B1 validates guest/table/session/menu ownership in public `create_order` | PARTIAL_CURRENT | Database-level same-partner relationship invariant is not universal; other public service graphs still require review; see AA-002 in the architecture audit |
| Global Telegram identity | One Telegram identity can have separate guest/employee contexts per partner; only superuser rebinds Telegram ID or edits direct global identity Admin, while a venue owner/authorised employee editor can update their employee's `@username` | `TelegramAccountAdmin` is platform-only; request-bound `EmployeeProfileAdminForm` disables Telegram ID but permits `@username` for authorised tenant editors | CONFIRMED_CURRENT | `@username` is physically shared and therefore its owner-approved change is reflected wherever that Telegram identity appears; trusted Telegram-update synchronization remains the source of runtime profile facts |
| Guest registration/profile | `/start` creates/reuses guest profile and customer code; profile shows loyalty context | `bot/handlers/start.py`, `apps/users/services.py`, `bot/handlers/profile.py` | CONFIRMED_CURRENT | Keep code/path characterization when evolving profile UX |
| Loyalty-only journey | No forced table/order UI; cashier can record manual sale/bonus | Bot content capability construction and quick-sale/manual purchase services | PARTIAL_CURRENT | Define tariff rule: loyalty always enabled or configurable |
| Menu browse | Active categories/items; optional browsing without session | `apps/menu/repositories.py`, `bot/handlers/menu.py`, `PartnerBotSettings` flags | CONFIRMED_CURRENT | No direct `apps/menu` test module found in audit inventory |
| Configurable guest labels/templates | Per-partner display text with fallback and unambiguous routed actions | `apps/partners/models.py:PartnerBotSettings.clean`; `bot/services/content.py`; B1 duplicate-label regression test | PARTIAL_CURRENT | B1 rejects duplicate actionable reply labels; raw text remains the routing mechanism, so stable intent routing is still a future design choice |
| QR table activation | Partner-scoped QR validation, visit, table session | `apps/tables/services.py:activate_table_session`; start/session handlers | PARTIAL_CURRENT | Concurrent scans can create multiple active sessions for same guest; no model constraint |
| Multiple guests at one table | Individual guest sessions/orders; shared operational table context | `TableSession.guest/table`, guest/session selectors and billing flows | CONFIRMED_CURRENT | Cross-tenant FK integrity remains a platform invariant gap |
| Table-session expiry | `timed_out` state has defined lifecycle/UX | `TableSession.Status.TIMED_OUT` exists | CODE_IS_BEHIND_SPEC | No identified runtime expiry processor or complete lifecycle |
| Cart | Personal active cart, quantity controls, checkout snapshot | `apps/orders/services.py` cart methods and bot order handlers | CONFIRMED_CURRENT | Table-change abandonment timing and concurrency need characterization |
| Table order creation | Valid non-empty items create order, order items/history, staff notification | `apps/orders/services.py:create_order*`; B1 common-partner validation in generic `create_order` | PARTIAL_CURRENT | B1 rejects foreign guest/table/session/menu inputs; database-level relationship integrity and other owner services remain to be hardened |
| Order state machine | Only allowed transitions; audit history and staff/guest notification | `ORDER_STATUS_TRANSITIONS`, `transition_order_status`, `OrderStatusHistory` | PARTIAL_CURRENT | Transition uses a potentially stale order rather than locking a fresh row; duplicate callback hazard |
| Guest receipt confirmation | Guest can confirm only their eligible active-session order | `apps/orders/services.py:confirm_order_received_by_guest` locks selected order | CONFIRMED_CURRENT | Confirm boundary should remain covered by regression tests |
| Staff access | Active bound employee sees partner staff UX by role/capability | `bot/handlers/staff.py:_resolve_staff_employee_by_telegram_id`; `apps/employees/selectors.py`; B1 guards stale order callbacks with current module capability | PARTIAL_CURRENT | B1 now requires active user + active profile + matching partner; a formal per-role transition policy (PQ-004) remains open |
| Staff calls | Guest at active table notifies relevant staff roles | `bot/handlers/guest_call.py`, notification services/preferences | PARTIAL_CURRENT | Cooldown uses a check-then-create pattern and needs concurrency protection |
| Personal/shared billing | Same-context eligible orders make/extend bills; custom split creates request | `apps/billing/services.py` bill/request use cases | PARTIAL_CURRENT | Need concurrency-safe order-item allocation and DB ownership invariants |
| Payment and settlement | Valid payment transitions bill, orders and session follow-up | `apps/billing/services.py:record_payment`, session settlement helpers | PARTIAL_CURRENT | Bonus redemption locks bill, not guest; concurrent redemptions need an explicit invariant |
| Bonus visit | Active visit programmes accrue on QR activation | `apps/tables/services.py:activate_table_session` invokes `apply_bonus_programs(...VISIT)` | CONFIRMED_CURRENT | One active session rule must be hardened so visits cannot race |
| Bonus manual purchase / quick sale | Auditable staff sale and manual-purchase accrual | `apps/bonuses/services.py:register_walk_in_sale`, `apply_manual_purchase_bonus`; staff FSM | CONFIRMED_CURRENT | Immutable ledger/admin edit policy needs enforcement |
| Bonus after order completion | Cashback/milestone program has a defined controlled trigger | `BonusProgram.TriggerEvent.ORDER_COMPLETED` and strategies exist | CODE_IS_BEHIND_SPEC | No production caller found from order completion; owner must choose completed, paid or both |
| Admin tenant RBAC | Tenant owners see only their scope; admin access differs from staff access | `core/admin_mixins.py`, `AdminAccessProfile`, section permissions; B1 hides partner list filters, rejects foreign partner inline writes, keeps Telegram ID/direct identity Admin superuser-only, and permits staff `@username` through scoped employee editing | PARTIAL_CURRENT | Direct operational/money mutation bypasses remain unresolved |
| Admin operational actions | Admin invokes safe service transitions rather than raw edits | Safe actions exist in selected apps | CODE_IS_BEHIND_SPEC | Order/session/bill fields and billing-request bulk actions provide alternate paths |
| Staff notifications | On-commit dispatch, persisted delivery status/error | `apps/notifications/services.py`, `tasks.py`, `StaffNotification`; B1 recipient queries require active user/profile and matching partner | PARTIAL_CURRENT | Retry/delivery semantics and task-time recipient revalidation remain open |
| Guest status updates | Status change schedules guest notification where enabled | `apps/orders/services.py:transition_order_status` calls notification path | CONFIRMED_CURRENT | Inherits order-transition concurrency issue |
| Broadcast campaigns | Admin/scheduled queue sends partner marketing audience and records counts | `BroadcastCampaign`, `notifications.tasks`, Celery beat registration | DEFECT | Telegram send occurs inside `@transaction.atomic`; no durable per-recipient/lease idempotency |
| Scheduled broadcasts | Due campaigns are enqueued automatically | `apps/notifications/services.py:process_scheduled_broadcast_campaigns`; `tasks.py` | PARTIAL_CURRENT | Scheduled task is dynamically registered; CHEATSHEET’s old task list is incomplete |
| Daily operations report | On-demand operational report and scheduled partner metrics | `apps/analytics/services.py`, analytics task | CONFIRMED_CURRENT | Large partner data materialization may become a performance risk |
| Polling runtime | Active valid polling bot starts with token-to-partner runtime mapping and rechecks runtime status before every update | `bot/services/runtime.py`, `bot/services/context.py`, `bot/middlewares/partner_runtime.py`, `manage.py runbot` | CONFIRMED_CURRENT | Suspended partner receives the approved response; setting `is_active=False` stops application handling immediately, while stopping polling transport requires process restart |
| Webhook runtime | Production webhook endpoint/lifecycle/observability | `BotInstance.mode` stores mode | FUTURE_SCOPE | Runtime explicitly rejects webhook mode |
| Delivery / pickup | Full address/contact/time/fulfilment journeys | Flags validated in `PartnerBotSettings.clean` | FUTURE_SCOPE | No established handlers/services/state flow; do not claim current support |
| POS/fiscal/online payments | Provider contracts, callbacks/reconciliation/refunds | Model fields provide limited groundwork | FUTURE_SCOPE | Requires dedicated product and architecture decision |
| WebApp | Optional rich UI while bot remains fallback | No current application journey identified | FUTURE_SCOPE | Requires independent product/technical plan |

## Cross-cutting acceptance criteria requiring characterization tests

The following contracts are high risk even where an MVP path exists. They must be tested before an implementation batch changes adjacent code:

1. Partner A cannot read, mutate, receive a notification for, or attach a relation to Partner B data.
2. A disabled `User` or `EmployeeProfile` cannot use `/staff` or a previously issued staff callback.
3. Duplicate QR scans, order transition callbacks, bill allocation requests, payment/redemption requests and Celery deliveries do not create duplicate business effects.
4. Direct admin UI cannot bypass the same order/bill/session/bonus state rules as the bot/service path.
5. A content-label collision cannot cause the wrong guest intent to execute.
6. PostgreSQL-specific constraints/locks protect production invariants; SQLite-only tests are not treated as proof of concurrent correctness.

## Explicitly not accepted as proof of implementation

- a field/enum/feature flag without a reachable journey;
- a legacy statement headed “current implementation” without code evidence;
- a model-level status without a transition processor;
- a Celery task name without idempotency and delivery semantics;
- a tenant-filtered changelist without scoped related choices, actions, inlines and background paths;
- a test count or lint claim copied from `PROJECT_CHEATSHEET.md`.
