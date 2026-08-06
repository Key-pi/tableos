# TableOS — open product decisions

**Status:** owner decisions required before behavior-changing implementation.  
**Rule:** this audit records choices; it does not choose them silently. Until a choice is approved, application behavior remains unchanged and a roadmap batch may add only characterization tests/documentation.

## Decision register

| ID | Decision | Why it matters | Options to approve | Current evidence / status |
| --- | --- | --- | --- | --- |
| PQ-001 | Is loyalty/profile always included in every tariff? | Determines capability model, onboarding and entitlement checks | Always-on; tariff-controlled; free base + paid extensions | Legacy TZ calls it base, but a commercial entitlement rule is not formalized — AMBIGUOUS_PRODUCT_DECISION |
| PQ-002 | When does an order earn bonuses? | Controls financial ledger, customer expectation and idempotency key | On `completed`; on full `paid`; only when both; configurable per programme | **B3 baseline:** paid settlement dispatches the existing `ORDER_COMPLETED` programme once per source key; the event name is compatibility-only and partner-specific trigger expansion remains open — PARTIALLY_RESOLVED |
| PQ-003 | Who may cancel an order and until which state? | Changes customer promise, staff authority and state machine | Staff roles only; guest before `accepted`; manager override; time-window policy | Legacy TZ leaves this open — AMBIGUOUS_PRODUCT_DECISION |
| PQ-004 | Which staff roles may view/change which order states? | Needed for capability gating and stale callback authorization | Broad operational access; per-role transition matrix; assignment-only model | Current staff UI/capability checks are not a formal policy — AMBIGUOUS_PRODUCT_DECISION |
| PQ-005 | Should staff see all table orders or only assigned/role-relevant ones? | Affects operations, privacy and selectors/reporting | All partner orders; table-scoped; assigned plus manager override | Open in legacy TZ — AMBIGUOUS_PRODUCT_DECISION |
| PQ-006 | What is the authoritative semantics of `completed`? | Affects order list, bonuses, reports and session closure | Receipt complete; operational completion; paid-and-received finality; split status/display policy | Legacy TZ correctly says completed can be unpaid; final UX contract still needs explicit approval — NEEDS_FOLLOW_UP |
| PQ-007 | What happens when a guest moves tables with unsettled work? | Determines QR activation and cart/session policy | Block move with explanation; close old session but retain debts; staff-mediated transfer; explicit reassignment | Existing activation closes prior session and cart cleanup occurs later; desired conflict flow is not designed — AMBIGUOUS_PRODUCT_DECISION |
| PQ-008 | Should table sessions expire automatically? | Determines `timed_out` lifecycle, cleanup and customer/staff UX | No expiry; fixed inactivity window; venue-configurable window; staff-only closure | `timed_out` is declarative but no full lifecycle is implemented — AMBIGUOUS_PRODUCT_DECISION |
| PQ-009 | How should custom bill split work? | Directly changes money allocation model and staff UX | Per item; per amount; per guest; staff manual allocation; combinations | Current custom split is a request, not a settlement editor — AMBIGUOUS_PRODUCT_DECISION |
| PQ-010 | Is partial payment a staff-facing MVP operation? | Defines cashier UI, receipts and bill lifecycle | Full payments only; manual amount entry; split tender; provider-assisted | Core supports partial payment but product UI scope is not settled — AMBIGUOUS_PRODUCT_DECISION |
| PQ-011 | Is online payment / prepayment needed in first production release? | Requires provider callbacks, fraud/retry/refund contracts | No; pay-by-link; deposit; full online payment | Future scope in legacy TZ — AMBIGUOUS_PRODUCT_DECISION |
| PQ-012 | What delivery/pickup journey is required and when? | Existing flags have no user journey; data design depends on answer | Defer; pickup first; delivery first; both as parallel flows | Flags exist but no viable delivery/pickup flow — CODE_IS_BEHIND_SPEC / FUTURE_SCOPE |
| PQ-013 | Does a suspended partner stop an already-running bot immediately? | Affects incident response, legal/commercial suspension semantics and cache policy | **Approved for B1:** a suspended partner receives `Работа бота приостановлена, ожидайте обновлений.` for messages and callback alerts; a disabled bot instance processes no updates | `PartnerRuntimeMiddleware` and the live runtime resolver recheck `Partner.status` and `BotInstance.is_active` before dispatch — RESOLVED_FOR_B1 |
| PQ-014 | What delivery guarantee is promised for broadcasts? | Determines lease/outbox/recipient ledger design and customer-facing counts | At-least-once; best-effort with duplicate tolerance; effectively-once per campaign/recipient; manual reconciliation | Current task can retry/concurrently send without a durable cursor/lease — AMBIGUOUS_PRODUCT_DECISION |
| PQ-015 | What reliability guarantee is promised for operational staff/guest notifications? | Separates business success from communication delivery | Best effort; retry with observable failure; escalation/manual queue; provider fallback | Legacy says delivery failure must not undo business event; exact retry semantics are unspecified — NEEDS_FOLLOW_UP |
| PQ-016 | How should configurable labels be made safe? | Identical reply labels can choose the first matching handler | **Approved for B1:** reject duplicate actionable labels; stable hidden intent commands and context-specific collision rules remain future choices | `PartnerBotSettings.clean()` now rejects duplicate/non-empty routed reply labels before settings are saved — RESOLVED_FOR_B1 |
| PQ-017 | Are partner admin users allowed to directly edit operational/money state? | Direct fields/actions can bypass audit trail and transitions | Read-only ledger/state + safe actions; limited corrective workflow with reason/audit; platform-only override | **B2/B3 slice:** lifecycle, allocation, payment, bonus balance and ledger fields are readonly; bonus payment is a staff/service command, not a special Admin action. Correction authority remains PQ-018 — PARTIALLY_RESOLVED |
| PQ-018 | What correction workflow is allowed for bonus ledger and balances? | Needs auditability and financial reconciliation | Immutable compensating entries only; manager adjustment with reason; platform-only correction | Future work mentioned, no canonical policy — AMBIGUOUS_PRODUCT_DECISION |
| PQ-019 | Who can mutate a global Telegram identity? | A partner administrator changing it may affect contexts for another partner | **Approved for B1:** only superuser can rebind Telegram ID or use direct global identity Admin; the venue owner (and Employees-section staff authorised by that owner) may change a venue employee's `@username`, name and other profile data | `TelegramAccountAdmin` is platform-only; request-bound employee form disables Telegram ID but permits `@username` for an authorised tenant editor; scoped `UserAdmin` permits the venue owner's employee username/name edits — RESOLVED_FOR_B1 |
| PQ-020 | What tenant relation guarantee is required at the database layer? | Service-only checks do not protect imports/admin/future callers | App-level validation only; DB constraints where feasible + services; redesign ownership relations | Cross-partner FKs are currently representable — SECURITY/TENANCY_RISK requiring owner/architecture decision |
| PQ-021 | What languages/locales are required? | Affects templates, staff UX, reporting and Telegram content | Partner locale only; guest locale fallback; multilingual content management | Open in legacy TZ — AMBIGUOUS_PRODUCT_DECISION |
| PQ-022 | Are inventory/stop-lists, modifiers, service charge and tips in product scope? | Changes menu snapshots, pricing and billing | Exclude; one/more MVP additions; integrate provider inventory | Listed as future/open, no approved scope — FUTURE_SCOPE |
| PQ-023 | Is a WebApp a replacement or optional enhancement? | Prevents duplicating business rules and fragmented UX | Bot fallback + WebApp; bot only; WebApp primary later | Legacy proposes fallback; no implementation scope — AMBIGUOUS_PRODUCT_DECISION |
| PQ-024 | What analytics retention/export/privacy policy is needed? | Determines snapshots, access control and operational data lifecycle | On-demand only; scheduled retention; exports; dashboard; data deletion policy | Current metrics/report exist, product policy is incomplete — NEEDS_FOLLOW_UP |
| PQ-025 | Which capabilities are commercial entitlements versus technical flags? | Avoids flags becoming an ungoverned product/tariff layer | Separate entitlement policy; technical flags only; partner plan rules | Legacy requests tariff limits; no canonical decision — AMBIGUOUS_PRODUCT_DECISION |

## Decisions that should precede high-risk roadmap batches

1. **PQ-014, PQ-017, PQ-020** — delivery guarantee, administrative mutation and tenant relation integrity alter security/financial guarantees.
2. **PQ-002, PQ-006, PQ-007, PQ-009** — order/bonus/session/bill semantics determine the correct state-machine and migration tests.
3. **PQ-004, PQ-005** — staff authority must be settled before further callback hardening.

## Decisions that do not block characterization testing

The team can safely add regression tests documenting today’s behavior for tenant scoping, disabled users, duplicate callbacks, bill allocation, admin mutations and broadcast retry behavior. Such tests must label any behavior that conflicts with this register rather than treating it as desired product behavior.

## Resolved principles carried forward from legacy documentation

These are treated as accepted product principles unless the owner explicitly changes them:

- one shared platform may serve many partner bots;
- global Telegram identity does not merge partner-scoped guest/employee contexts;
- admin RBAC and Telegram staff access are different systems;
- several guests may use one physical table while carts/orders remain guest-owned;
- completed order and paid order are not automatically synonymous;
- notification delivery failure does not undo a successfully persisted business event;
- polling is the current runtime; webhook remains future scope;
- modules shape capability/UX, not separate product forks.
- B3 establishes `pay_bill_with_bonuses`: one bonus equals one UAH, the partner programme determines the maximum applied amount, a monetary remainder stays payable, and full coverage records the normal `bonuses` payment method. Paid settlement is only the current order-reward baseline; trigger expansion, refunds/cancellations and revenue labels remain open.
- B1 approves non-empty, unique actionable reply labels for one partner bot;
  a move to stable callback/intent routing remains a separate decision.
- B1 suspension policy: a suspended partner receives the standard paused-work
  response; a disabled bot instance processes no update. Stopping the polling
  transport itself still requires the runtime process to be restarted.
- B1 staff-identity policy: only a superuser may rebind Telegram ID or edit a
  `TelegramAccount` directly. The venue owner (and an Employees-section editor
  they authorise) may change their employee's `@username`; the owner may also
  edit that venue user's username/name. Since Telegram `@username` is stored on
  a shared identity, its display change is owner-approved and can be visible in
  every context that uses that same Telegram account. Telegram data received
  from Telegram itself and trusted management/bootstrap commands remain system
  integration paths.
