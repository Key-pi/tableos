# TableOS — Architecture audit

**Mode:** `AUDIT_AND_GOVERNANCE`  
**Audit date:** 2026-07-27  
**Scope:** every first-party source module, model, migration, admin/form, bot handler/router, Celery task, test module, root configuration, compose file, and the required root documents. No application code was changed.

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
