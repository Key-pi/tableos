# TableOS — Architecture audit

**Mode:** `AUDIT_AND_GOVERNANCE`  
**Audit date:** 2026-07-27  
**Scope:** every first-party source module, model, migration, admin/form, bot handler/router, Celery task, test module, root configuration, compose file, and the required root documents. No application code was changed.

> **Reading this register.** Findings AA-001–AA-019 and their line-numbered
> evidence describe the audit baseline commit `b17ce5b`; they are preserved as
> historical evidence and are not rewritten when code moves. The B1–B3 update
> sections record what changed. Findings AA-020–AA-032 come from the
> [post-B3 review](#post-b3-review--2026-10-23) at commit `ddb7dce` and cite code
> as `path:symbol`. The current implemented state is in
> [`../architecture/AS_IS_ARCHITECTURE.md`](../architecture/AS_IS_ARCHITECTURE.md).

## Method and terminology

The source tree and executable baseline are the primary evidence. `README.md`, `PROJECT_CHEATSHEET.md`, and `TABLEOS_TZ.md` are important product/documentary evidence but are not silently treated as executable truth. File-by-file coverage is in [`FILE_COVERAGE.md`](FILE_COVERAGE.md); inventory and commands are in [`CODEBASE_INVENTORY.md`](CODEBASE_INVENTORY.md) and [`BASELINE.md`](BASELINE.md).

Severity is the impact if a supported/authorized flow is exercised: **High** can break tenant isolation, money/state integrity, or durable delivery; **Medium** causes material incorrect behavior or blocks a specification; **Low** is a contained operational or disclosure issue. Confidence describes how directly the behavior was established from source, tests, or a controlled admin probe. `AMBIGUOUS_PRODUCT_DECISION` means code must not guess the policy.

## Executive risk register

| ID | Category | Severity | Confidence | Short conclusion | Roadmap batch |
|---|---|---:|---:|---|---|
| AA-001 | SECURITY / PERMISSION | High | High | Disabled user can remain a staff actor. | B1 |
| AA-002 | SECURITY / TENANCY_RISK | High | High | Same-partner graph is not a durable invariant. | B1 |
| AA-003 | BUG / FINANCIAL_INTEGRITY | High | High | Admin can bypass lifecycle and ledger services. | B2 |
| AA-004 | BUG / CONCURRENCY | High | High | Order state transition uses stale unlocked row. | B2 |
| AA-005 | BUG / CONCURRENCY | High | High | One guest can obtain concurrent active table sessions. | B2 |
| AA-006 | BUG / FINANCIAL_INTEGRITY | High | High | One order item can be allocated to two bills. | B3 |
| AA-007 | BUG / FINANCIAL_INTEGRITY | High | High | Loyalty wallet is not serialized or source-idempotent. | B3 |
| AA-008 | BUG / ASYNC | High | High | Broadcast holds transaction over Telegram I/O and lacks lease/idempotency. | B4 |
| AA-009 | BUG / PERMISSION | Medium | High | Suspension and stale staff capability are not rechecked at execution. | B1 |
| AA-010 | BUG / ROUTING | Medium | High | Partner button-label collisions are nondeterministic. | B1 |
| AA-011 | CODE_IS_BEHIND_SPEC | Medium | High | `ORDER_COMPLETED` loyalty trigger has no runtime dispatch. | B3 |
| AA-012 | BUG / DATA_VALIDATION | Medium | High | Quick sale accepts non-positive amount. | B3 |
| AA-013 | AMBIGUOUS_PRODUCT_DECISION | Medium | High | Analytics mixes gross quick-sale and net bill payment as revenue. | B3 |
| AA-014 | AMBIGUOUS_PRODUCT_DECISION | Medium | High | Full bonus redemption can leave an unpayable zero bill. | B3 |
| AA-015 | SECURITY / TENANCY_RISK | Medium | High | Admin filters, inlines, and global Telegram identity have gaps. | B1 |
| AA-016 | TEST_GAP | Medium | High | No PostgreSQL concurrency/admin-isolation characterization coverage. | B1–B4 |
| AA-017 | PREMATURE_ABSTRACTION / NAMING_PLACEMENT | Medium | High | Interfaces/repositories/tasks are uneven and mostly unconsumed. | B5 |
| AA-018 | DOCUMENTATION_DRIFT | Medium | High | Root docs claim stale tests/lint/deployment paths and local links. | B0 |
| AA-019 | BUG / RELIABILITY | Low | High | Demo seed is non-atomic and can leave partial identity changes. | B5 |

## Findings

### AA-001 — inactive Django users retain staff authority

- **Category / severity / confidence:** `SECURITY / PERMISSION`, High, High.
- **Evidence:** `apps/employees/selectors.py:11-16` filters `EmployeeProfile.is_active` but not `EmployeeProfile.user.is_active`. `bot/handlers/staff.py:842-858` gates `/staff` through that resolver, and `apps/orders/services.py:290-299` resolves an actor in the same manner.
- **Current behavior:** disabling a `User` does not itself prevent an otherwise active employee profile from opening staff menus or executing staff actions.
- **Why it matters:** user disablement is expected to be an immediate access revocation boundary for orders, payment and bonus operations.
- **Target behavior:** every staff actor resolution requires an active user, an active employee profile, matching partner, and the current capability.
- **Risk:** former or suspended staff can perform authorized-but-unintended mutations after the account is disabled.
- **Roadmap batch:** B1 — tenant, actor, and capability guardrails.
- **Verification:** characterization tests for disabled user across `/staff`, order, billing, notification and bonus callbacks; then a regression suite that proves no service accepts the actor.

### AA-002 — same-partner relationships are not durably enforced

- **Category / severity / confidence:** `SECURITY / TENANCY_RISK`, High, High.
- **Evidence:** `core/database/models.py:22-33` supplies only a `partner` FK. `TableSession` (`apps/tables/models.py:43-66`), order/cart relations, billing allocation/payment relations, menu references, bonus references, employee references and notification references can link records whose individual `partner` FKs differ. `apps/orders/services.py:112-182` accepts guest/table/session/menu objects without universal common-partner validation.
- **Current behavior:** normal bot paths usually scope each lookup to the bot partner, but neither the database nor all public services make a mismatched object graph impossible. Admin inline and direct-service paths widen the future misuse surface.
- **Why it matters:** a tenant boundary based only on caller discipline is not sufficient for money, guest data, or staff authorization.
- **Target behavior:** owner services validate a common partner before mutation; schema-level constraints/unique keys are added wherever expressible; admin and data migration checks reject invalid graphs.
- **Risk:** a future caller, admin permission change, script, or bug can create cross-tenant links that later cause data exposure or foreign order settlement.
- **Roadmap batch:** B1 for guards and characterization; B3 for financially critical durable constraints/migration plan.
- **Verification:** PostgreSQL tests attempting mismatched relations through services/admin; a data audit query before migration; no cross-partner row can be created via supported write surfaces.

### AA-003 — Django Admin bypasses lifecycle and financial invariants

- **Category / severity / confidence:** `BUG / FINANCIAL_INTEGRITY`, High, High.
- **Evidence:** ordinary `OrderAdmin` changes expose status/totals and mutable item inlines (`apps/orders/admin.py:47-55,98-143`); `BillAdmin` exposes bill financial fields and allocation inlines (`apps/billing/admin.py:23-32,107-118,180-198`); `TableSessionAdmin` exposes state/relations (`apps/tables/admin.py:306-313`); `BonusTransaction` is editable without a wallet reconciliation (`apps/bonuses/admin.py:36-48`). `BillingRequestAdmin` bulk actions use `queryset.update()` (`apps/billing/admin.py:72-88`) despite service guards at `apps/billing/services.py:786-803`.
- **Current behavior:** a scoped, permitted administrator can take direct model writes that do not execute owner services, state transition validation, totals reconciliation, audit history, or post-commit side effects.
- **Why it matters:** Django Admin is a production write surface, not merely a debugging tool; it can corrupt money, status history, session closure and loyalty ledger consistency.
- **Target behavior:** lifecycle/ledger/allocation/derived-total fields become readonly or unavailable; explicit admin actions invoke owner services under the same authorization and transaction rules as bot flows.
- **Risk:** inconsistent bills/orders, invalid billing-request transitions, mismatched wallet balances, and silently missed notifications.
- **Roadmap batch:** B2 for state/write protection; B3 for financial controls.
- **Verification:** Admin integration tests for each restricted field/action; unsupported direct changes are rejected and supported actions generate the same state/history/side effects as service calls.

### AA-004 — staff order transitions are not serialized

- **Category / severity / confidence:** `BUG / CONCURRENCY`, High, High.
- **Evidence:** `transition_order_status()` is transactional but uses its passed `Order` instance rather than reloading with `select_for_update()` (`apps/orders/services.py:185-232`). Staff obtains an order before calling it (`bot/handlers/staff.py:1437-1457`). The guest confirmation flow does use a locked reload (`apps/orders/services.py:235-287`).
- **Current behavior:** concurrent callbacks can each validate a stale `accepted` state, write different transitions/history rows, and last-write wins on the order row.
- **Why it matters:** staff status is a state machine with operational and notification consequences; history must describe the actual sequence.
- **Target behavior:** reload canonical order under lock, validate transition inside that lock, make repeated callback behavior explicit and idempotent.
- **Risk:** false histories, conflicting assignment/status, duplicate notices.
- **Roadmap batch:** B2.
- **Verification:** PostgreSQL concurrent-transition test plus duplicate callback test; exactly one permitted transition/history/notification results.

### AA-005 — active table session invariant races across tables

- **Category / severity / confidence:** `BUG / CONCURRENCY`, High, High.
- **Evidence:** `apps/tables/models.py:43-66` has no partial unique constraint for active `(partner, guest)` sessions. `activate_table_session()` locks the selected `Table`, not the guest or existing guest session (`apps/tables/services.py:43-91`).
- **Current behavior:** two scans by the same guest at different tables can lock different rows and create two active sessions; lookup later selects the newest one (`apps/tables/services.py:94-104`).
- **Why it matters:** session is the authority for cart, order, billing and visit bonus context.
- **Target behavior:** adopt a documented canonical lock (normally guest) and a PostgreSQL durable active-session invariant or an equivalent serialized design.
- **Risk:** orders/bills/bonus credits attach to an unintended table/session.
- **Roadmap batch:** B2.
- **Verification:** PostgreSQL two-worker test on different table rows; one active session remains and the losing request has deterministic behavior.

### AA-006 — bill allocation can duplicate an order item

- **Category / severity / confidence:** `BUG / FINANCIAL_INTEGRITY`, High, High.
- **Evidence:** `BillItem` lacks a uniqueness constraint for `order_item` (`apps/billing/models.py:147-175`). `attach_orders_to_bill()` performs check-then-create work without a common transaction/lock (`apps/billing/services.py:134-182`); `create_bill_from_orders()` does the same without locking order/item rows (`:252-309`).
- **Current behavior:** two concurrent bill-building operations can both see a free item and attach it to different bills.
- **Why it matters:** one ordered item must not be collectible by two bills.
- **Target behavior:** define allocation ownership, lock the canonical rows, and add an expressible durable uniqueness/integrity rule with a data migration plan.
- **Risk:** duplicate collection, settlement corruption, misleading reports.
- **Roadmap batch:** B3 — financial integrity.
- **Verification:** PostgreSQL concurrency test and a constraint-level test; data audit shows no pre-existing duplicate allocations before rollout.

### AA-007 — loyalty wallet mutation lacks serialization and durable source linkage

- **Category / severity / confidence:** `BUG / FINANCIAL_INTEGRITY`, High, High.
- **Evidence:** `apply_bonus_programs()` updates an in-memory guest balance without `select_for_update()` (`apps/bonuses/services.py:32-80`). `redeem_bonus_for_bill()` locks a bill but not `primary_guest` (`apps/billing/services.py:583-633`). `BonusTransaction` (`apps/bonuses/models.py:51-88`) has neither a Bill/WalkInSale source FK nor an idempotency key; bill association is merely a comment. Visit strategy checks “already today” without a lock/constraint (`apps/bonuses/strategies.py:50-63`).
- **Current behavior:** parallel bills/quick sales/credits can overdraw or lose-update the balance and duplicate a source event.
- **Why it matters:** the wallet and ledger are financial records and must agree.
- **Target behavior:** one documented guest-wallet lock order, immutable source-linked ledger entries, idempotency semantics per source event, and explicit compensation/refund policy.
- **Risk:** negative/incorrect balance, duplicated rewards, unreconcilable ledger and analytics.
- **Roadmap batch:** B3.
- **Verification:** PostgreSQL parallel redemption/accrual tests; reconciliation query proves ledger-derived balance equals stored balance for fixtures.

### AA-008 — broadcast delivery mixes transaction with network I/O and has no durable lease

- **Category / severity / confidence:** `BUG / ASYNC`, High, High.
- **Evidence:** `send_broadcast_campaign_now()` remains in a transaction while calling Telegram and sleeping on retry (`apps/notifications/services.py:440-547`). `apps/notifications/tasks.py:59-72` fetches the campaign without a lease, cursor, recipient delivery record, or idempotency guard. Telegram failures are converted into failed results, weakening Celery retry semantics.
- **Current behavior:** workers/retries can perform duplicate sends and update aggregate counters ambiguously while a database transaction remains open around variable-latency external I/O.
- **Why it matters:** a broadcast is a side effect that cannot be rolled back with the database transaction; long transactions also harm contention.
- **Target behavior:** commit a durable delivery intent first, claim recipients with a lease/idempotency key, send outside the transaction, then persist the result in a short follow-up transaction.
- **Risk:** duplicate customer messages, incorrect campaign reporting, locked rows and unreliable recovery after worker crash.
- **Roadmap batch:** B4 — async delivery reliability.
- **Verification:** task retry/parallel-worker/crash-recovery tests proving at most one send per recipient/campaign policy and no network call while a source transaction is open.

### AA-009 — runtime suspension and staff capabilities are not consistently live-checked

- **Category / severity /confidence:** `BUG / PERMISSION`, Medium, High.
- **Evidence:** startup runtime rejects suspended partners in `bot/services/runtime.py:21-79`, but per-update context in `bot/services/context.py:5-7` loads a partner without a suspension check. Callback routes including `stafforderopen`, `stafforder`, `stafforderrefresh`, `staffnotiforder`, `staffnotifaccept` resolve an employee but do not consistently re-check `_can_view_open_orders` after a staff card is rendered.
- **Current behavior:** a post-start suspension does not prove that polling stops processing the partner; a stale staff callback can retain a capability that was later removed.
- **Why it matters:** operator suspension and permission removal are security control points, not merely display preferences.
- **Target behavior:** define a single live partner/actor/capability guard used before each protected command; product owner chooses whether legacy in-flight cards are denied, refreshed, or converted to a read-only response.
- **Risk:** users act under revoked tenant or module permission until process restart/card expiry.
- **Roadmap batch:** B1.
- **Verification:** test suspend-after-start and capability-remove-after-card scenarios; each protected handler/service is denied with a deterministic user-facing response.

### AA-010 — configurable reply labels can collide and misroute bot input

- **Category / severity / confidence:** `BUG / ROUTING`, Medium, High.
- **Evidence:** exact text filters in `bot/filters/content.py:10-21` are evaluated under router ordering; `PartnerBotSettings.clean()` at `apps/partners/models.py:251-286` does not prohibit collisions among action labels.
- **Current behavior:** two configured actions with the same or stale text match the first applicable handler rather than a stable product action.
- **Why it matters:** tenant configuration can alter the semantics of ordinary guest/staff messages and lead to wrong lifecycle commands.
- **Target behavior:** model/form validation establishes a collision policy, and handlers use stable callback/action identifiers wherever reply-text ambiguity cannot be eliminated.
- **Risk:** incorrect routing, unexpected state mutation, difficult support diagnosis.
- **Roadmap batch:** B1.
- **Verification:** form/model validation tests and router tests with duplicate, renamed, and stale labels.

### AA-011 — `ORDER_COMPLETED` bonus programs are configured but not executed

- **Category / severity / confidence:** `CODE_IS_BEHIND_SPEC`, Medium, High.
- **Evidence:** `ORDER_COMPLETED` is a default trigger in `apps/bonuses/models.py:9-24` and seeded configurations. Repository-wide call analysis finds production `apply_bonus_programs()` calls for `VISIT` in `apps/tables/services.py:83-90` and `MANUAL_PURCHASE` in `apps/bonuses/services.py:83-98,151-159`, but none from an order lifecycle.
- **Current behavior:** a configured completion cashback/milestone program does not accrue through normal order flow.
- **Why it matters:** the legacy specification presents these programs as product functionality, while actual behavior does not deliver it.
- **Target behavior:** owner decides the business event (`completed`, `paid`, cancelled/refunded treatment); then an idempotent source-linked order bonus is dispatched after that transition.
- **Risk:** customers do not receive advertised rewards or receive them at a product-undefined time if patched ad hoc.
- **Roadmap batch:** B3.
- **Verification:** product decision record, service-level event tests, duplicate transition tests, and ledger/order source reconciliation.

### AA-012 — quick sale accepts zero or negative amounts

- **Category / severity / confidence:** `BUG / DATA_VALIDATION`, Medium, High.
- **Evidence:** `register_walk_in_sale()` converts to `Decimal` but does not require `amount > 0` (`apps/bonuses/services.py:102-122`); `apps/bonuses/forms.py:35-61` does not add the guard. `WalkInSale.amount` and `MenuItem.price` lack an explicit positive validator/check (`apps/bonuses/models.py:91-129`, `apps/menu/models.py:28-60`).
- **Current behavior:** an authorized admin flow can create a non-positive sale; it then enters bonus and analytics logic without a refund lifecycle.
- **Why it matters:** revenue and loyalty calculations assume a sale is positive.
- **Target behavior:** require a positive monetary amount at form, service, and durable schema levels; model refunds/voids as an explicit separate lifecycle if required.
- **Risk:** negative revenue, unintended bonus effect, untraceable correction records.
- **Roadmap batch:** B3.
- **Verification:** unit/admin tests for zero/negative rejection and database constraint test; product-approved refund flow has separate tests if introduced.

### AA-013 — revenue semantics mix gross quick sales and net paid bills

- **Category / severity / confidence:** `AMBIGUOUS_PRODUCT_DECISION`, Medium, High.
- **Evidence:** `WalkInSale.net_amount` represents amount after bonuses (`apps/bonuses/models.py:120-129`), yet analytics sums `WalkInSale.amount` (gross) alongside actual `Payment.amount` (net) in `apps/analytics/services.py:136-144,179-235`.
- **Current behavior:** a 180 sale with 90 bonus redemption reports 180, while a bill flow reports its 90 payment; both are shown under revenue.
- **Why it matters:** reports can be materially misleading even though individual source records are valid.
- **Target behavior:** product owner chooses and names cash/net revenue, gross sales, bonus-discount, and any combined dashboard metric; implementation exposes them separately if needed.
- **Risk:** incorrect financial decision making and impossible reconciliation with payment records.
- **Roadmap batch:** B3 after explicit metric decision.
- **Verification:** approved metric examples including bonus redemption, reconciliation fixtures, and analytics report tests for each named metric.

### AA-014 — full bonus redemption leaves a zero bill without settlement policy

- **Category / severity / confidence:** `AMBIGUOUS_PRODUCT_DECISION`, Medium, High.
- **Evidence:** `BonusProgram.max_redeem_share` has no strict upper bound below 100 (`apps/bonuses/models.py:31-42`); redemption can reduce a bill to `0.00` (`apps/billing/services.py:615-634`), while `record_payment()` rejects non-positive amounts (`:528-530`).
- **Current behavior:** with a 100% configuration, a zero-total bill can remain issued/open because no payment can close it.
- **Why it matters:** order/session settlement and financial reporting lose a coherent terminal state.
- **Target behavior:** owner chooses either a redemption cap below 100% or an explicit zero-payment settlement transition that closes the bill and records the non-cash consideration.
- **Risk:** stranded bills/orders/sessions and inaccurate payment/bonus reporting.
- **Roadmap batch:** B3 after owner decision.
- **Verification:** configuration boundary tests and end-to-end zero-total settlement test for the chosen policy.

### AA-015 — secondary admin tenant boundaries and identity ownership are incomplete

- **Category / severity / confidence:** `SECURITY / TENANCY_RISK`, Medium, High.
- **Evidence:** `ScopedAdminMixin` scopes normal querysets but not the `partner` list filter (`core/admin_mixins.py:79-87`); an audited owner account with one partner saw four global partner filter choices. Standard child inlines are not themselves scoped; `save_formset` sets partner only when empty, preserving an explicitly cross-tenant relation. `EmployeeProfileAdminForm.save()` updates global Telegram identity data (`apps/employees/forms.py:65-83`) from a tenant-admin form.
- **Current behavior:** ordinary object access remains scoped in tested top-level admins, but partner names/IDs can be disclosed; future child permissions can make inlines a cross-tenant write surface; a venue administrator can change global Telegram identity used by another tenant.
- **Why it matters:** tenant isolation includes metadata disclosure and authority over globally shared identity.
- **Target behavior:** scope/remove tenant filter choices, use explicitly scoped inline forms/querysets, and move global identity edits behind a product-approved identity/consent rule.
- **Risk:** low-grade tenant enumeration now; material cross-tenant writes/identity mutation after permission configuration changes.
- **Roadmap batch:** B1.
- **Verification:** admin request tests for list filters, related field choices, inlines and identity conflict cases under two tenants.

### AA-016 — critical concurrency, permission and admin paths lack characterization tests

- **Category / severity / confidence:** `TEST_GAP`, Medium, High.
- **Evidence:** 17 test modules cover ordinary flows, but no inspected tests exercise concurrent TableSession/Order/BillItem/wallet/broadcast execution, inactive staff, live suspension, stale capability, label collision, admin filter/inline isolation, or direct-admin lifecycle writes. Baseline SQLite test success does not model production PostgreSQL locking.
- **Current behavior:** regressions in the highest-risk paths can pass the existing suite.
- **Why it matters:** refactoring stateful/financial code without characterization makes behavior changes indistinguishable from fixes.
- **Target behavior:** each approved batch begins with focused PostgreSQL-capable and admin/bot authorization tests, then makes the smallest supported correction.
- **Risk:** accidental behavioral breakage and false confidence from SQLite-only test runs.
- **Roadmap batch:** B1–B4, as a precondition within each batch.
- **Verification:** test matrix in the roadmap is green against PostgreSQL; tests fail on the audited behavior where a defect is being fixed.

### AA-017 — layer labels hide unused abstractions and a task/service cycle

- **Category / severity / confidence:** `PREMATURE_ABSTRACTION / NAMING_PLACEMENT`, Medium, High.
- **Evidence:** AST/import analysis found no first-party consumers for `SnapshotBuilder`, `BonusCalculator`, `ShiftTracker`, `CatalogPublisher`, `NotificationGateway`, `OrderNotifier`, `PartnerScopedService`, `SessionResolver`, or `TelegramIdentityProvider`. `apps/employees/services.py` and `apps/menu/services.py` contain only docstrings; most task modules are placeholders. `apps.notifications.services ↔ apps.notifications.tasks` forms the direct static SCC through a function-local task import.
- **Current behavior:** folders named services/selectors/repositories/interfaces signal architecture but do not consistently correspond to a meaningful responsibility.
- **Why it matters:** mechanical abstraction makes ownership and dependency direction harder to audit; deleting without dynamic/API proof would be risky.
- **Target behavior:** retain only evidence-backed ports/repositories, consolidate reads deliberately, and break the notifications scheduling dependency without changing public task names prematurely.
- **Risk:** future code follows misleading seams; unreviewed cleanup can break dynamic import/registration or external consumers.
- **Roadmap batch:** B5 — cleanup after behavior is characterized.
- **Verification:** static and dynamic registration search, public import check, focused tests, then one narrow removal/consolidation per approved change.

### AA-018 — required root documentation is not an accurate operational source

- **Category / severity / confidence:** `DOCUMENTATION_DRIFT`, Medium, High.
- **Evidence:** at the audit baseline, `PROJECT_CHEATSHEET.md` said 66 tests pass and Ruff passes, contained absolute local links, and omitted dynamically registered scheduled broadcast processing; the reproducible baseline records 139 passing tests and 21 pre-existing Ruff errors/54 files needing formatting. The documentation-only B0 correction preserves this evidence while adding a status banner, repo-relative links and current counts. Deployment prose still differs from `docker-compose.yml`, which defines web/worker/DB/Redis but no bot/beat process. `TABLEOS_TZ.md` specifies behavior that current source does not always implement, including order-completed loyalty dispatch and live runtime controls.
- **Current behavior:** multiple root documents compete as apparent truth and can mislead implementation/review.
- **Why it matters:** stale operational instructions and undocumented code/spec divergence create unsafe changes and unreliable handoff.
- **Target behavior:** short root onboarding; canonical product/architecture/engineering/audit documents under `docs/`; legacy TZ retained as source material with an explicit migration map.
- **Risk:** contributors use wrong commands, incorrectly assume a feature exists, or deploy an incomplete process topology.
- **Roadmap batch:** B0 — documentation governance, completed in this audit artifact set.
- **Verification:** root links are repo-relative; canonical documents link to the baseline and feature matrix; all listed contradictions appear in this audit rather than being silently overwritten.

### AA-019 — demo seed is non-atomic around identity/profile work

- **Category / severity / confidence:** `BUG / RELIABILITY`, Low, High.
- **Evidence:** the demo seed path performs multiple related user/profile/configuration operations without the atomic boundary used by the baseline seed. A global Telegram username reassignment can occur before a later employee-profile partner conflict fails.
- **Current behavior:** a failed demo bootstrap can leave a subset of global identity changes committed.
- **Why it matters:** demo/operations tooling is still a write path and may contaminate subsequent tests or local troubleshooting.
- **Target behavior:** make seed intent idempotent and atomic, or document compensating recovery with an explicit dry-run/validation stage.
- **Risk:** partial local/demo data and misleading staff identity ownership.
- **Roadmap batch:** B5.
- **Verification:** injected failure test proves rollback/no partial identity mutation; rerun is idempotent.

## Additional audit conclusions

### Tenant isolation

Normal bot reads have a good starting pattern: trusted partner comes from bot-token runtime and most selectors scope by it. That positive result does not close AA-002/AA-015 because database relationships, public service arguments, and Admin represent independent enforcement layers. No direct cross-tenant Telegram exploit path was proven in the reviewed standard guest flows; the documented risks are durable/latent write-boundary failures, not an asserted current customer-data breach.

### Transactions, async work, and external I/O

The code correctly uses `transaction.on_commit()` for several ordinary order notifications and uses `sync_to_async` around Django ORM calls in the bot. The broadcast path is the material exception. The target must not introduce a generic outbox/event-bus framework merely because it is a familiar pattern; B4 first establishes the narrow recipient lease/idempotency requirement.

### ORM and performance

The critical defects are integrity rather than a simple N+1 count. Two lower-priority observations remain: up to ten configured `PartnerButtonFilter` checks can cause repeated partner/content lookups for an unmatched message, and analytics report construction materializes data then filters lists per table. Both require representative query/latency measurement before any optimization; no cache or architectural subsystem is justified by this audit alone.

### Documents versus code

The documents created by this audit preserve legacy claims as evidence and classify their relationship to source. Product decisions are deliberately left open where the code cannot establish the desired rule (notably completed-versus-paid bonus timing, revenue semantics, zero-total settlement, and stale-card behavior). They must receive owner approval before implementation.

## B1 implementation update — 2026-08-03–04

The findings above preserve the audited baseline. The following table records
the approved B1 implementation without rewriting that evidence.

| Finding | B1 result | Remaining boundary |
|---|---|---|
| AA-001 | Staff resolution and staff-notification recipient selection now require active Django user, active employee profile, and partner consistency. | A formal per-role authorization matrix is still PQ-004. |
| AA-002 | Generic `create_order` now rejects foreign guest, table, table-session, and menu-item input before mutation. | The database cannot yet enforce all cross-table partner relations; other public service graphs require separate review. |
| AA-009 | Staff order callbacks recheck current orders-module capability before use. B1 runtime middleware/context recheck every update; a suspended partner receives the approved paused-work response and a live-disabled bot instance is dropped. | Polling transport itself still ends on an operational restart. |
| AA-010 | `PartnerBotSettings.clean()` rejects duplicate/non-empty routed reply labels. | Raw reply-text routing remains; a stable-intent migration was not selected. |
| AA-015 | Tenant Admin no longer receives a global partner list filter and foreign partner inline instances are rejected. Non-superusers cannot rebind Telegram ID or use direct global identity Admin, but the venue owner/authorised employee editor can change their staff member's `@username`; the owner can edit venue-user username/name. | Operational/money Admin bypasses are B2/B3. |
| AA-016 | Added focused service, bot, model-validation, notification, and Admin characterization tests. | PostgreSQL locking/constraint coverage is still required for B2–B4. |

The B1 technical controls are verified by the focused Django test suite in
local SQLite mode. It is not evidence of a PostgreSQL concurrency invariant;
the owner approved and B1 implemented the former PQ-013/PQ-019 policies on
2026-08-04.

## B2 implementation update — 2026-08-06

The approved B2 batch keeps the audited findings above as historical evidence
and records the bounded remediation:

| Finding | B2 result | Remaining boundary |
|---|---|---|
| AA-003 | Order/cart/bill/allocation/session lifecycle and derived fields are read-only in Admin; explicit order, bill, table-session and BillingRequest actions call owner services. BillingRequest actions no longer use queryset `.update()`. | Financial allocation/ledger correction policy remains B3. |
| AA-004 | `transition_order_status()` reloads the current order under `select_for_update()` before validating and writing history/notifications. | PostgreSQL concurrent execution is included as a gated test and was not runnable locally. |
| AA-005 | Table activation locks `GuestProfile → Table → active sessions`; the partial unique active-session constraint prevents duplicate scopes and preserves latest-scan-wins behavior. | Production migration requires the same duplicate audit before rollout; PostgreSQL two-table scan execution is environment-gated. |
| AA-016 | Added B2 stale-state, Admin bypass, service-action and PostgreSQL-only concurrency characterization tests. | Local verification remains SQLite-only because Docker/PostgreSQL was unavailable. |

The local data audit immediately before the migration found 2 active
`TableSession` rows and no duplicate active `(partner, guest)` scopes. The
migration does not repair or delete data.

## B3 payment-flow update — 2026-08-06

The owner clarified the bonus-payment UX after the initial B3 shape. The
canonical command is now `apps/billing/services.py:pay_bill_with_bonuses`:
it applies the partner-program cap at a 1:1 bonus-to-UAH rate, persists a
source-linked redemption, and leaves any monetary remainder for ordinary
cash/terminal payment. Full coverage creates the normal
`Payment.Method.BONUSES` record for the nominal bonus value in that same
command; `Bill.paid_amount` counts money payments only. The bot exposes one
“Оплатить бонусами” intent; the previous separate redeem/zero-settlement Admin
path is removed (the old callback remains only as a compatibility alias).

The paid-order reward trigger remains a baseline compatibility mapping. The
partner programme model already carries trigger values, and future batches may
add partner-specific trigger dispatch; no universal product rule is inferred
from the current paid baseline. Refund/cancellation and revenue semantics stay
deferred.

## Post-B3 review — 2026-10-23

**Scope:** a full re-read of documentation, models, services, Admin, Celery
tasks and bot handlers at commit `ddb7dce` (branch `clean-project-rules`).
**Method:** source reading and call-chain tracing. The test suite was not
executed in this review (no Python environment with project dependencies was
available); AA-027 was confirmed by executing the isolated function. Every
finding below must receive a characterization test before its fix, as required
by `AGENTS.md`.

### Risk register (post-B3)

| ID | Category | Severity | Confidence | Short conclusion | Roadmap batch |
|---|---|---:|---:|---|---|
| AA-020 | BUG / FINANCIAL_INTEGRITY | High | High | Billing, bill-request visibility and auto-close are scoped to the table's whole history, not the current visit. | B3.2 (PQ-026 decided, ADR-0007) |
| AA-021 | BUG / FINANCIAL_INTEGRITY | High | Medium–High | Post-payment auto-close can raise `TableSessionError` and roll back a payment; adapters do not handle it. | B3.1 — resolved |
| AA-022 | BUG / FINANCIAL_INTEGRITY | High | High | A cancelled order stays charged in its bill; bills cannot be cancelled and allocations cannot be released. | B3.2 part A — resolved |
| AA-023 | BUG / ADMIN_BYPASS | Medium | High | `OrderAdmin` leaves `guest`, `table`, `table_session` editable. | B3.1 — resolved |
| AA-024 | BUG / RELIABILITY | Medium | High | Text is sent with HTML parse mode but never escaped. | B3.1 — resolved |
| AA-025 | BUG / STATE_MACHINE | Medium | High | Guest receipt confirmation bypasses `ORDER_STATUS_TRANSITIONS`. | B3.1 — resolved |
| AA-026 | SECURITY / PERMISSION | Medium | High (conditional) | Tenant `UserAdmin` does not exclude superusers that carry the tenant's `partner`. | B3.1 — resolved |
| AA-027 | BUG / DATA_QUALITY | Low | High (executed) | Checking out via the reply button stores part of the button label as the order comment. | B3.1 — resolved |
| AA-028 | BUG / PERFORMANCE | Low | High | The Bill changelist recalculates and saves every listed bill on a GET. | B3.1 — resolved |
| AA-029 | BUG / TIME_BOUNDARY | Low | High | Visit bonus "once per day" uses the UTC day; the daily metric snapshot never revisits the previous day. | B3.1 — resolved |
| AA-030 | BUG / ASYNC | Low | High | Notification tasks re-send on retry and do not recheck the recipient at delivery time. | B4 |
| AA-031 | NAMING_PLACEMENT / LAYERING | Low | High | `core` imports domain apps; unused unscoped selectors and a legacy bonus service remain. | B5 |
| AA-032 | DOCUMENTATION_DRIFT | Medium | High | Canonical docs described pre-B1 runtime, cited moved line numbers and claimed PostgreSQL tests that do not exist. | B0.1 (resolved by this review) |
| AA-033 | BUG / FINANCIAL_INTEGRITY | High | High (executed) | On PostgreSQL every bill creation failed: `FOR UPDATE` on a nullable outer join. | B3.1 (resolved) |
| AA-034 | OPERATIONS / PORTABILITY | Low | Medium | `Europe/Kiev` is a legacy timezone alias that recent tz databases ship only as an optional package. | B5 |

### AA-020 — billing and settlement use the table's history instead of the current visit

- **Category / severity / confidence:** `BUG / FINANCIAL_INTEGRITY`, High, High.
- **Evidence:** `apps/billing/services.py:get_table_billable_orders` returns every non-cancelled order with `table_id` that is not yet in a bill, with no session or time filter; `get_or_create_personal_bill_for_guest_table` and `get_or_create_shared_bill_for_table` build bills from it. `_close_table_sessions_if_fully_settled` refused to close if any order ever placed at the table was unsettled (replaced in B3.1 by the session-scoped `_close_settled_sessions_of_bill`). `bot/services/navigation.py:resolve_guest_navigation_state` shows "request bill" when any order ever existed at the table. `apps/tables/services.py:activate_table_session` closes a guest's previous session without checking unpaid orders, and there is no session expiry.
- **Current behavior:** unpaid orders of a guest who left or moved to another table remain attached to the table. The next party's shared bill includes them, a guest re-scanning the same table later gets them in a personal bill, and auto-close after payment never succeeds again for that table.
- **Why it matters:** guests can be charged for other people's orders, and staff see permanent "unsettled" tables.
- **Target behavior:** billing eligibility and settlement checks are scoped to an explicit visit (active session or sessions), and orphaned unpaid orders follow an owner-approved policy.
- **Roadmap batch:** B3.2. PQ-026 is decided (ADR-0007: join-or-new visit on scan, staff closes the old visit); PQ-007 stays open.
- **Verification:** two consecutive parties at one table with an unpaid order from the first; assert the second party's shared bill, request button and auto-close behaviour.

### AA-021 — post-payment auto-close can roll back a payment

- **Category / severity / confidence:** `BUG / FINANCIAL_INTEGRITY`, High, Medium–High (traced, not executed).
- **Evidence:** `apps/billing/services.py:_process_post_payment_updates` → `_close_table_sessions_if_fully_settled` checks open bills, carts and order settlement at table level, then calls `apps/tables/services.py:close_table_session_if_settled` for each active session. That function raises `TableSessionError` when `can_close_table_session` finds an `open`/`auto_prepared` billing request for the session — for example a custom-split request — which the table-level pre-check does not consider (pending requests are processed only after the loop). The exception propagates out of `record_payment` / `pay_bill_with_bonuses`. `bot/handlers/staff.py:staff_bill_pay_callback_handler` catches only `OrderFlowError` and `BillingServiceError`; `BillAdmin._record_remaining_payment` catches only `BillingServiceError`; `PaymentAdmin.save_model` catches nothing.
- **Current behavior:** the payment transaction rolls back; the staff callback raises an unhandled exception without answering; the Admin returns an error page.
- **Why it matters:** cash that was physically taken cannot be recorded, and the cashier receives no explanation.
- **Target behavior:** recording a payment never fails because of a secondary session-closing step. Auto-close either skips non-closable sessions or follows an owner-approved rule.
- **Roadmap batch:** B3.1. PQ-027 decided: auto-close only sessions whose orders are all paid, never failing the payment.
- **Verification:** table with an open custom-split request; record the final payment through the service, staff callback and Admin; assert the payment persists and the session outcome follows PQ-027.

### AA-022 — cancelled orders remain charged; bills cannot be cancelled

- **Category / severity / confidence:** `BUG / FINANCIAL_INTEGRITY`, High, High.
- **Evidence:** `apps/orders/services.py:transition_order_status` allows `canceled` from every non-final state and does not look at bill allocation. `apps/billing/services.py:recalculate_bill_totals` sums all `BillItem.line_total` regardless of order status. No service, handler or Admin action sets `Bill.Status.CANCELED`. `BillItem.unique_order_item_bill_allocation` is unconditional, so an allocated `OrderItem` can never be billed elsewhere.
- **Current behavior:** cancelling an order that is already in a draft/issued bill leaves its lines in the bill total; the guest is asked to pay for it. A wrong bill cannot be voided, and its items cannot be re-billed.
- **Why it matters:** overcharging customers and no correction path for staff.
- **Target behavior:** an owner-approved policy for cancelling an allocated order (block, or remove lines and recalculate before payment) and an explicit bill cancel/release command that frees allocations.
- **Roadmap batch:** B3.2. PQ-028 decided: owner-granted bill change/void with audit history; total never below the paid amount.
- **Verification:** cancel an order inside a draft bill and inside a partially paid bill; cancel a draft bill and re-bill its orders; PostgreSQL constraint behaviour if the allocation constraint becomes conditional.

### AA-023 — order relations are editable in Admin

- **Category / severity / confidence:** `BUG / ADMIN_BYPASS`, Medium, High.
- **Evidence:** `apps/orders/admin.py:OrderAdmin.readonly_fields` protects status, money and timestamps but not `guest`, `table`, `table_session` (or `comment`). `core/tests_admin.py:test_order_admin_hides_lifecycle_and_derived_fields` checks lifecycle/derived fields only.
- **Current behavior:** a tenant user with Orders change permission can move an order to another guest, table or session of the same partner, including an order that is already billed or paid.
- **Why it matters:** bill context, personal-bill eligibility, bonus accrual target and session settlement depend on these relations; B2 intended relation fields to be protected.
- **Target behavior:** relation fields read-only after creation; any reassignment is an explicit owner-service command.
- **Roadmap batch:** B3.1.
- **Verification:** Admin form test asserting the fields are read-only for tenant and platform users.

### AA-024 — HTML parse mode without escaping

- **Category / severity / confidence:** `BUG / RELIABILITY`, Medium, High.
- **Evidence:** `bot/main.py:run_polling` and `apps/notifications/services.py:_send_telegram_messages` set `ParseMode.HTML` by default. No module escapes interpolated values; partner name, menu item names, Telegram usernames, order comments, bill labels and employee names are inserted raw into bot messages and notifications.
- **Current behavior:** Telegram rejects any message containing a bare `&`, `<` or `>` outside a tag (for example a venue named "Bar & Grill"). Handlers raise `TelegramBadRequest`; notifications are recorded as failed.
- **Target behavior:** every dynamic value is escaped at the presentation boundary; templates that intentionally contain markup are treated as trusted partner content and documented.
- **Roadmap batch:** B3.1.
- **Verification:** rendering tests with `&`, `<`, `>` in each interpolated field.

### AA-025 — guest receipt confirmation bypasses the transition table

- **Category / severity / confidence:** `BUG / STATE_MACHINE`, Medium, High.
- **Evidence:** `apps/orders/services.py:confirm_order_received_by_guest` moves any order that is not `new`, `completed` or `canceled` to `completed`. `ORDER_STATUS_TRANSITIONS` does not allow `accepted → completed` or `preparing → completed`.
- **Current behavior:** two sources of truth for the order state machine; a guest can complete an order the kitchen has not marked ready.
- **Target behavior:** an explicit guest rule: confirmation from any non-final status, including `new`, recorded as a guest action (PQ-029, decided).
- **Roadmap batch:** B3.1.
- **Verification:** state-matrix test covering staff and guest paths.

### AA-026 — tenant user list can contain superusers

- **Category / severity / confidence:** `SECURITY / PERMISSION`, Medium, High (conditional).
- **Evidence:** `apps/users/admin.py:UserAdmin` inherits `ScopedAdminMixin.get_queryset`, which filters by `partner_id` only. `ScopedAdminMixin._scoped_related_queryset` excludes superusers from foreign-key choices, but the changelist, change form and password-change view do not. `AdminAccessProfile.save` sets `User.partner` for the linked user.
- **Current behavior:** if a superuser ever has `partner` set to a venue (manually or through an access profile), that venue's owner can open the superuser's account and reset its password. Seeds do not create this state today.
- **Target behavior:** tenant user querysets always exclude superusers and platform-only accounts.
- **Roadmap batch:** B3.1.
- **Verification:** Admin request test with a partner-linked superuser.

### AA-027 — reply-button checkout writes the label into the order comment

- **Category / severity / confidence:** `BUG / DATA_QUALITY`, Low, High (function executed).
- **Evidence:** `bot/handlers/order.py:checkout_handler` passes the message text to `_split_checkout_comment`, which returns everything after the first space. The message is the button label itself; the default label `Оформить заказ` yields the comment `заказ`.
- **Current behavior:** every order placed through the reply button has a comment equal to the tail of the label. It appears in the staff order card (`Комментарий: заказ`) and in guest status messages.
- **Target behavior:** the reply button never produces a comment; a comment, if supported, comes from an explicit input step.
- **Roadmap batch:** B3.1.
- **Verification:** handler test with default and multi-word custom labels.

### AA-028 — Bill changelist writes on read

- **Category / severity / confidence:** `BUG / PERFORMANCE`, Low, High.
- **Evidence:** `apps/billing/admin.py:BillAdmin.remaining_amount` (a `list_display` column) calls `apps/billing/services.py:get_bill_remaining_amount`, which reloads the bill and calls `recalculate_bill_totals`, which saves the bill.
- **Current behavior:** opening the bill list performs an extra query and an unlocked write per row; a concurrent payment can be overwritten by stale totals in a narrow window.
- **Target behavior:** read paths compute the remaining amount without saving.
- **Roadmap batch:** B3.1.
- **Verification:** query-count test for the changelist; no `save` on GET.

### AA-029 — time boundaries ignore the venue timezone

- **Category / severity / confidence:** `BUG / TIME_BOUNDARY`, Low, High.
- **Evidence:** `apps/bonuses/strategies.py:_visit_bonus` computes "today" as `timezone.now().replace(hour=0, …)`, i.e. midnight UTC. `apps/analytics/services.py:refresh_daily_partner_metrics` refreshes only the current local day, every 15 minutes.
- **Current behavior:** in Kyiv the visit-bonus day starts at 02:00 or 03:00 local time. Payments made after the last run of a day are never included in that day's `PartnerDailyMetric`.
- **Target behavior:** day boundaries use `Partner.timezone`; the snapshot task also finalises the previous day.
- **Roadmap batch:** B3.1.
- **Verification:** tests around local midnight for both paths.

### AA-030 — notification tasks are not idempotent and do not recheck recipients

- **Category / severity / confidence:** `BUG / ASYNC`, Low, High.
- **Evidence:** `apps/notifications/tasks.py:deliver_staff_notification_task` and `send_guest_order_status_update_task` retry on any exception but do not skip a notification already `sent`; `deliver_staff_notification_now` does not recheck that the employee and user are still active.
- **Current behavior:** an exception after a successful send repeats the message; a disabled employee still receives notifications queued before the change.
- **Target behavior:** part of the B4 delivery contract (PQ-015).
- **Roadmap batch:** B4.

### AA-031 — layering leftovers

- **Category / severity / confidence:** `NAMING_PLACEMENT / LAYERING`, Low, High.
- **Evidence:** `core/admin_mixins.py` imports `apps.partners.models` and `apps.users.models`; `core/urls.py` imports order, partner and table models for the demo page. `apps/orders/selectors.py:get_order_with_items`, `get_cart_with_items` and `apps/tables/selectors.py:get_table_by_qr_token` are unscoped and have no callers; `get_orders_for_session` is unscoped by partner. `apps/billing/services.py:redeem_bonus_for_bill` is used only by tests since B3. Enum classes in `*/constants.py` duplicate the model `TextChoices` and have drifted (`orders/constants.py:PaymentMethod`, `notifications/constants.py:BroadcastStatus`). `core/logging/handlers.py` is not wired into `LOGGING` and its `partner_id` regex captures a single digit.
- **Roadmap batch:** B5, under ADR-0005 compatibility rules.

### AA-032 — documentation drift after B1–B3

- **Category / severity / confidence:** `DOCUMENTATION_DRIFT`, Medium, High.
- **Evidence:** before this review `AS_IS_ARCHITECTURE.md` described the pre-B1 runtime and cited `bot/dispatcher.py`, `config/celery.py`, `config/settings/base.py`; `BOT_RUNTIME.md` cited `bot/dispatcher.py`; most line-numbered citations pointed at moved code. `REFACTOR_ROADMAP.md` and `FEATURE_MATRIX.md` stated that B3 added PostgreSQL tests for allocation, redemption and accrual, but the repository contains only two PostgreSQL-gated tests (order transition, table activation). The payment lock order in `ADR-0003.md` and `TARGET_ARCHITECTURE.md` omitted `GuestProfile`, which the paid-order bonus accrual locks.
- **Resolution:** corrected in the B0.1 documentation refresh (see `REFACTOR_ROADMAP.md`). Current-state documents now cite `path:symbol`.

### AA-033 — bill creation fails on PostgreSQL

- **Category / severity / confidence:** `BUG / FINANCIAL_INTEGRITY`, High, High (executed).
- **Evidence:** `apps/billing/services.py:create_bill_from_orders` used `Order.objects.select_for_update().select_related("table", "guest")`. `Order.table` is nullable, so Django emits a `LEFT OUTER JOIN`, and PostgreSQL rejects the query with `FOR UPDATE cannot be applied to the nullable side of an outer join`. Introduced in B3 (`ddb7dce`). SQLite ignores `FOR UPDATE`, so the SQLite suite stayed green; the full suite on PostgreSQL 16 had 29 errors, all from this call.
- **Current behavior (before fix):** no bill could be created on PostgreSQL — guest bill requests, staff table bills and the Admin action all failed.
- **Resolution:** `select_for_update(of=("self",))`; the full suite passes on PostgreSQL 16.
- **Lesson:** run the whole suite on PostgreSQL before closing a batch that adds row locks (`TESTING.md`).

### AA-034 — legacy `Europe/Kiev` timezone name

- **Category / severity / confidence:** `OPERATIONS / PORTABILITY`, Low, Medium.
- **Evidence:** `.env.example` (`DJANGO_TIMEZONE`), `core/settings/env.py` and `Partner.timezone` default to `Europe/Kiev`. The IANA name is `Europe/Kyiv`; `Europe/Kiev` remains only as a backward-compatible link, which newer Debian/Ubuntu tz packages move to an optional `tzdata-legacy` package. On such a system Django refuses to start (`Incorrect timezone setting: Europe/Kiev`), and `ZoneInfo("Europe/Kiev")` in reports and bonus rules fails. Observed in the review container; macOS still ships the link.
- **Target behavior:** use `Europe/Kyiv` in defaults and migrate stored partner values, or add the `tzdata` Python package as a dependency; verify inside the production image.
- **Roadmap batch:** B5.

## B3.1 implementation update — 2026-10-23

Branch `b3.1-correctness`, verified with the full suite on SQLite and on
PostgreSQL 16 (199 tests; 5 PostgreSQL-only tests skipped on SQLite).

| Finding | Result |
|---|---|
| AA-033 | Bill creation locks only order rows (`of=("self",)`). |
| AA-021 | Auto-close (when enabled) closes only sessions whose orders are in the paid bill and fully settled; their pending requests are marked processed; nothing raises into the payment (PQ-027). |
| AA-025 | `GUEST_RECEIPT_CONFIRMABLE_STATUSES` / `can_guest_confirm_receipt`: guest confirmation from any non-final status including `new`, recorded as a guest action (PQ-029). |
| AA-023, AA-026 | Order `guest`/`table`/`table_session` read-only in Admin; tenant `UserAdmin` excludes superusers. |
| AA-027 | Reply-button checkout passes no comment. |
| AA-028 | `get_bill_remaining_amount` is read-only. |
| AA-029 | Venue-local day for visit bonus; previous-day metric finalised after local midnight. |
| AA-024 | Dynamic text escaped in bot messages, partner template values and notifications; broadcast text remains partner markup (B4). |
| AA-032 (tests) | PostgreSQL concurrency tests for bill allocation, bonus payment and final payment; a mutation check confirms they fail without the locks. |

Remaining from the post-B3 review: AA-020 (B3.2 part B), AA-030 (B4),
AA-031 and AA-034 (B5).

## B3.2 part A implementation update — 2026-10-23

AA-022 is resolved on branch `b3.2a-bill-changes`:

- `BillItem.is_active` keeps removed lines for history; the allocation
  constraint is now `unique_active_order_item_bill_allocation` (active lines
  only), so released order items can be billed again.
- `BillAdjustment` is the append-only change log (kind, actor, reason code,
  optional comment, totals before/after, payload). Only the author can fill
  an empty comment, within 30 minutes.
- `remove_bill_lines`, `void_bill` and `cancel_order` in
  `apps/billing/services.py` enforce the owner decisions: owner-granted
  permission (`EmployeeProfile.can_adjust_bills`, editable only by the venue
  owner) or the owner; reason required; no change below the paid amount; no
  line changes on a bill with applied bonuses (void only, which returns the
  bonuses as a `reversal` ledger credit); no cancellation of an order in a
  paid bill.
- `transition_order_status` refuses to cancel an order that still has active
  bill lines, checked after the order lock, so no adapter can leave a
  cancelled order charged.
- Bot: bill card buttons, edit view, reason buttons and optional comment;
  Admin: void and remove-line actions with a reason page, order cancel through
  the bill, adjustment history.
- Verified: 227 tests green on SQLite and PostgreSQL 16, including PostgreSQL
  races void-vs-payment and cancel-vs-billing.
