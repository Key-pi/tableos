# TableOS — Refactor roadmap

**Status (2026-10-23):** B0–B3, B0.1 (documentation refresh) and B3.1
(post-B3 correctness fixes) are complete. The full suite, including every
PostgreSQL-gated test, passes on PostgreSQL 16 in a development environment;
the production data audits for the B2/B3 migrations are still required before
rollout. B3.2 is complete: parts A (bill changes and void), B (table visits)
and C (cashback base). B4 (durable delivery, ADR-0008) and B5 (hygiene) are complete. Product work
continues per [ECOSYSTEM_DIRECTION.md](../product/ECOSYSTEM_DIRECTION.md).

## Operating rules for every batch

- Start from a clean, recorded baseline and preserve unrelated worktree changes.
- Add characterization tests before changing a confirmed behavior.
- Run concurrency and durable-constraint tests against PostgreSQL, not only
  `DJANGO_USE_SQLITE=true`.
- Keep public import paths, Celery task names, router registration, admin URLs,
  and user-visible behavior compatible unless the batch explicitly approves a
  migration/deprecation.
- Include partner/actor authorization, transaction/lock order, and external
  side-effect behavior in the code review description.
- Stop and escalate if a data audit finds cross-tenant or duplicate financial
  records; do not silently repair production-like data.

## Batch B0 — Documentation governance baseline

| Field | Proposal |
|---|---|
| **Goal** | Establish one navigable, evidence-backed source map without changing runtime behavior. |
| **Scope** | Root onboarding, canonical `docs/` product/architecture/engineering/audit structure, ADRs, repository instructions and skill. |
| **Not in scope** | Application code, models, migrations, dependencies, deployment changes, secret rotation. |
| **Evidence / risks** | AA-018; stale tests/lint claims, absolute local links, competing root documentation. |
| **Dependencies** | None. |
| **Required verification** | Link check, `git diff --check`, review that every source/spec contradiction appears in audit/feature matrix. |
| **Rollback** | Revert documentation-only commit; original legacy TZ remains preserved as evidence. |
| **Exit criterion** | Canonical documents and `AGENTS.md` exist, legacy source is explicitly mapped, owner accepts the governance structure. |

**Status in this audit:** artifacts have been prepared; owner approval is still
needed before any subsequent batch.

## Batch B1 — Tenant, actor and capability guardrails

| Field | Proposal |
|---|---|
| **Goal** | Make supported entry points consistently reject inactive, suspended, cross-tenant, or no-longer-capable actors. |
| **Scope** | Characterize and centralize active user + active employee + partner + capability checks; validate common-partner inputs in exposed owner services; recheck suspension/capability at update/callback execution; reject label collisions; scope/remove admin partner filter and related/inline choices; decide global Telegram identity editing policy. |
| **Not in scope** | New role system, redesigning bot UI, database trigger rollout, module extraction, unrelated cleanup. |
| **Evidence / risks** | AA-001, AA-002 (application layer), AA-009, AA-010, AA-015, AA-016. |
| **Dependencies** | Product decision for stale callback behavior and global identity ownership; test environment with two partners. |
| **Required verification** | Bot/admin/service tests for inactive user, inactive profile, wrong partner, suspended partner after startup, capability removal after card render, duplicate labels, list filter/inline/related choices. Existing guest flow authorization must remain green. |
| **Rollback** | Feature-flag or narrowly revert the guard helper/use sites; no schema migration required in the first B1 slice. |
| **Exit criterion** | Every protected command receives trusted partner/actor context and a characterization suite proves rejection before mutation. |

**Suggested sequence:** first write failing tests for AA-001/AA-009 and admin
scope; then introduce the smallest shared guard; finally validate config label
rules and document any consciously retained legacy callback response.

**Implementation update — 2026-08-03:** the approved technical slice is
implemented without a schema migration. It adds active `User` + active
`EmployeeProfile` + partner consistency to staff resolution and notification
recipient queries; validates guest/table/session/menu ownership in generic
`create_order`; rechecks current orders-module capability for stale staff order
callbacks; rejects duplicate/non-empty routed reply labels; and closes the
audited tenant Admin list-filter and inline-write gaps. On 2026-08-04 the owner
approved the remaining runtime/identity policies: a suspended partner receives
the standard paused-work response, a live-disabled bot instance processes no
update, and only a superuser can rebind Telegram ID or edit the direct global
identity Admin. The venue owner (or an Employees-section editor authorised by
that owner) can change a staff member's `@username`; the owner can also change
their venue user's username/name. The middleware/form implementation and
characterization tests cover those policies.

**B1 status: complete.** No schema migration was required. `is_active=False`
stops application handling immediately; stopping the polling transport itself
remains an operational process restart, not a B1 code path.

## Batch B2 — State machines, sessions, and safe Admin commands

| Field | Proposal |
|---|---|
| **Goal** | Serialize lifecycle transitions and remove direct Admin paths that bypass state rules. |
| **Scope** | Lock/reload order before transition; establish active-session lock/invariant strategy; make state/derived fields readonly or service-backed in Admin; replace `BillingRequestAdmin` raw updates; characterize direct order/bill/session edits and stale callbacks. |
| **Not in scope** | Loyalty ledger redesign, bill allocation schema, notification fan-out redesign, visual admin redesign. |
| **Evidence / risks** | AA-003, AA-004, AA-005, AA-016. |
| **Dependencies** | B1 actor guards; PostgreSQL test database; owner acceptance of Admin operations that remain allowed. |
| **Required verification** | PostgreSQL concurrent status and two-table scan tests; admin tests showing forbidden fields cannot be changed and approved actions use services; history/notification assertions; migration test if a partial session constraint is selected. |
| **Rollback** | Keep a reversible migration only after data audit; preserve prior admin action behind a temporary compatibility path only if it still enforces the owner service. |
| **Exit criterion** | One canonical state command per lifecycle, deterministic duplicate-callback behavior, and no supported Admin bypass. |

**Design checkpoint (resolved in B2):** canonical lock order is recorded in
ADR-0003 before the corresponding locks and constraint are applied.

**Implementation update — 2026-08-06:** B2 adds locked reloads for order
transitions, bill issue/payment, billing-request transitions and table-session
close; serializes table activation with `GuestProfile → Table → active session`
locks; and adds the partial unique active-session constraint after a local data
audit found no duplicate active guest scopes. Order/cart/bill/allocation/session
Admin forms now protect lifecycle/derived/relation fields, while approved
state changes use explicit owner-service actions. `BillingRequestAdmin` no
longer performs raw queryset updates. The full SQLite suite and Admin/service
characterization tests pass; PostgreSQL-only concurrency tests are included but
were not executable because the local Docker/PostgreSQL service was unavailable.

**B2 implementation status:** complete in application code; PostgreSQL
concurrency execution remains an environment verification prerequisite before
production rollout of the migration.

## Batch B3 — Financial integrity and loyalty semantics

| Field | Proposal |
|---|
| **Goal** | Make bill allocation, payment, bonus wallet, revenue reporting, and money validation reconcilable and concurrency-safe. |
| **Scope** | Data audit and migration plan for one `OrderItem` allocation; canonical wallet locking/source linkage/idempotency; positive quick-sale validation; Admin financial field restrictions; explicit `ORDER_COMPLETED` event decision; zero-total bill policy; revenue metric definitions and report updates. |
| **Not in scope** | Introducing a general accounting platform, payment provider integration, mass historical reclassification without owner-approved migration policy. |
| **Evidence / risks** | AA-002 (financial graph), AA-003, AA-006, AA-007, AA-011–AA-014, AA-016. |
| **Dependencies** | B1/B2 guards; owner decisions listed in `docs/product/OPEN_PRODUCT_QUESTIONS.md`; PostgreSQL; a reviewed data backfill/exception handling plan. |
| **Required verification** | Concurrent allocation/redemption/accrual tests; DB constraint tests; ledger-to-balance reconciliation fixture; negative/zero amount tests; completed/paid timing tests; zero-total settlement test; gross/net analytics examples. |
| **Rollback** | Expand/validate before enforcing constraints; retain reversible migrations where safe; do not auto-delete conflicting legacy records; pause rollout if data audit finds exceptions. |
| **Exit criterion** | No item is allocated twice, balance derives/reconciles from immutable source-linked entries, money mutation uses a documented lock order, and product metric/lifecycle choices are testable. |

**Owner decisions captured for B3:**

1. Paid settlement is the current baseline for order bonus accrual. Existing
   `ORDER_COMPLETED` programme values remain as a compatibility name; the
   partner programme model remains extensible for future trigger-specific
   dispatch instead of imposing one universal trigger.
2. Refund/cancellation after earned or redeemed bonuses is explicitly deferred.
3. A partner may configure up to 100% bonus coverage through its programme.
   One bonus equals one UAH. The single “pay with bonuses” command applies the
   allowed amount, leaves any monetary remainder for cash/terminal payment, and
   writes a `bonuses` payment for the nominal bonus value only when the bill is
   fully covered; `Bill.paid_amount` counts money payments only.
4. Gross/net/payment/bonus dashboard definitions remain deferred until the
   owner reviews concrete report examples.
5. Verification is intentionally risk-weighted: critical money, ledger,
   allocation and Admin paths are covered by SQLite tests; no blanket test is
   added for every presentation branch. *(Corrected 2026-10-23: PostgreSQL
   concurrency coverage exists only for order transitions and table
   activation; see AA-032.)*

**B3 implementation update — 2026-08-06:** B3 adds locked/source-linked bonus
ledger mutations, guest-balance reconciliation guards and per-partner
idempotency keys; dispatches the approved paid-order bonus event once after
settlement; validates positive quick-sale amounts; serializes `OrderItem`
allocation and adds a database uniqueness guard after a local audit found 36
active allocations with no duplicates; restricts direct bonus ledger/balance
Admin edits; and exposes one service-backed bonus-payment command that applies
partial coverage or closes a fully covered bill. The separate zero-total Admin
action was removed. Refund/cancellation semantics and revenue metric
definitions remain out of scope. *(Corrected 2026-10-23: the earlier text
said PostgreSQL-only allocation, redemption and accrual tests were included.
They are not in the repository; writing and running them is part of the
PostgreSQL verification gate and of B3.1.)*

## Batch B0.1 — Documentation refresh after B3 (complete, 2026-10-23)

| Field | Result |
|---|---|
| **Goal** | Bring canonical documents back in line with the code after B1–B3 and record the post-B3 review. |
| **Scope** | Rewrote `AS_IS_ARCHITECTURE.md` for commit `ddb7dce`; replaced line-number citations in current-state documents with `path:symbol`; marked audit-time snapshots as historical; corrected the PostgreSQL-test claims and the payment lock order; added AA-020–AA-032, PQ-026–PQ-030, ADR-0007 and batches B3.1/B3.2; updated engineering, runtime and background-job documents. |
| **Not in scope** | Application code, migrations, dependencies, runtime configuration. |
| **Verification** | Repository-wide check that every cited file exists and that `path:symbol` citations resolve; `git diff --check`. Tests were not executed (no environment); the recorded test baseline remains the 2026-07-27 run. |
| **Rollback** | Revert the documentation commit. |

## Batch B3.1 — Post-B3 correctness fixes (no schema change) — complete, 2026-10-23

| Field | Proposal |
|---|---|
| **Goal** | Fix the post-B3 defects that need no new data model, applying the owner decisions of 2026-10-23 where they apply. |
| **Scope** | AA-021: a payment always persists; auto-close (when enabled) closes only sessions whose orders are all paid by settled bills and never raises into the payment (PQ-027). AA-025: explicit guest transition rule — receipt confirmation from any non-final status, including `new`, recorded as a guest action (PQ-029). AA-023: order `guest`/`table`/`table_session` read-only in Admin. AA-024: HTML escaping at the presentation boundary. AA-026: tenant `UserAdmin` excludes superusers. AA-027: the checkout reply button never produces a comment. AA-028: no write on the Bill changelist. AA-029: venue-timezone day for visit bonus; snapshot finalises the previous day. Missing PostgreSQL tests for allocation, bonus redemption and paid-order accrual. |
| **Not in scope** | Visit model, bill editing/void, programme flags (B3.2); broadcast delivery (B4). |
| **Evidence / risks** | AA-021, AA-023–AA-029, AA-032. |
| **Dependencies** | PQ-027 and PQ-029 (decided). A working Python environment; PostgreSQL for the gated tests. |
| **Order** | One finding per commit: characterization test that reproduces the defect → fix → full suite. AA-021 first. |
| **Required verification** | Each finding's verification line in the audit register; full suite on SQLite; PostgreSQL-gated tests on PostgreSQL. |
| **Rollback** | Revert the individual commit; no migrations. |
| **Exit criterion** | Each finding has a regression test that failed before and passes after. |

**B3.1 result — 2026-10-23:** all scope items done, plus AA-033 (bill
creation failed on PostgreSQL), found when the suite was first run on
PostgreSQL. Each fix has a regression test that failed before the change;
the new billing concurrency tests fail when the row locks are removed. Full
suite: 199 tests green on SQLite (5 PostgreSQL-only skipped) and on
PostgreSQL 16. Details: [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md#b31-implementation-update--2026-10-23).

## Batch B3.2 — Table visits, bill changes and programme options (schema change)

| Field | Proposal |
|---|---|
| **Goal** | Implement the owner decisions that need new data: visit-scoped billing, auditable bill changes, cashback base option. |
| **Design** | [`../architecture/B3_2_VISITS_AND_BILL_CHANGES.md`](../architecture/B3_2_VISITS_AND_BILL_CHANGES.md) — approved 2026-10-23; parts A (bill changes), B (visits), C (cashback base) ship separately. **Parts A (AA-022), B (AA-020) and C (PQ-030) are complete.** |
| **Scope** | 1) **Table visit** (ADR-0007, PQ-026, AA-020): visit model and backfill migration; join-or-new choice when scanning a table with an active visit; staff flag for tables with several visits and manual close of the old visit; billing eligibility, "request bill" button and settlement checks scoped to the visit. 2) **Bill changes and void** (PQ-028, AA-022): owner-granted permission to change or void bills; remove/add lines, including lines of a cancelled order; recalculation; audit history with actor, time and reason; total may not fall below the paid amount; void releases allocations (the unconditional `unique_order_item_bill_allocation` becomes conditional on the bill not being void). 3) **Cashback base option** (PQ-030): per-programme flag, default = accrue on the full total. |
| **Not in scope** | Refunds, guest table moves (PQ-007), broadcast delivery. |
| **Evidence / risks** | AA-020, AA-022, ADR-0007. Open design points to settle in the batch plan: how a bill with applied bonuses is changed (block vs. compensating ledger entry), and how historical sessions are grouped into visits by the backfill. |
| **Dependencies** | B3.1 merged; data audit on a production-like copy before the migrations; PostgreSQL. |
| **Required verification** | Two parties at one table with an unpaid order from the first; join and new-visit flows; concurrent scans on PostgreSQL; bill change/void with history and permission checks for owner, granted and non-granted staff; constraint migration forward/backward. |
| **Rollback** | Reversible migrations; feature can be shipped behind a partner setting until verified. |
| **Exit criterion** | No order can be billed outside its visit; every bill change is attributable; a voided bill's orders can be billed again. |

## Batch B4 — Durable async delivery and operational runtime

| Field | Proposal |
|---|---|
| **Status** | **Complete** (2026-10-23, branch `b4-delivery`, ADR-0008). Details: [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md#b4-implementation-update--2026-10-23). |
| **Goal** | Make notification/broadcast delivery recoverable without database transactions spanning Telegram I/O. |
| **Scope** | Separate commit from send; introduce the smallest durable delivery/lease/idempotency record necessary for campaign fan-out; align exception/retry policy; recheck recipient eligibility as product requires; document/deploy actual bot/worker/scheduler topology. |
| **Not in scope** | A generic event bus, message broker replacement, microservice extraction, unmeasured caching. |
| **Evidence / risks** | AA-008, AA-009, AA-016, AA-018, AA-030; `docker-compose.yml` has no bot or beat service. |
| **Dependencies** | B1 live eligibility semantics; owner decision on delivery guarantee and recipient preference behavior; operational deployment owner. |
| **Required verification** | Worker retry, duplicate task, concurrent worker, crash-after-send/before-persist, disabled recipient, and campaign counter tests; instrumentation showing no network I/O in the source transaction; compose/runbook review. |
| **Rollback** | Retain original campaign records and make the new delivery worker opt-in per campaign version until recovery behavior is proven; migration must preserve unsent work. |
| **Exit criterion** | Delivery attempt has durable identity/lease/outcome, retries are intentional, and operational documentation names every required process. |

## Batch B5 — Evidence-backed consolidation and operational hygiene

| Field | Proposal |
|---|---|
| **Status** | **Complete** (2026-10-23). Details: [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md#b5-implementation-update--2026-10-23). |
| **Goal** | Remove only proven-unused scaffolding and tighten lower-risk reliability/performance issues after behavior is protected. |
| **Scope** | Resolve empty services/tasks/interfaces/repositories through static + dynamic consumer proof; break notifications task/service cycle; make demo seed atomic/idempotent; measure and address confirmed filter/report performance issues. |
| **Not in scope** | Broad folder renames, code formatting sweep, dependency upgrade, speculative cache/event-engine abstraction. |
| **Evidence / risks** | AA-017, AA-019, AA-031 and lower-priority observations in architecture audit. |
| **Dependencies** | Characterization coverage from B1–B4; public import compatibility plan (ADR-0005). |
| **Required verification** | Import/API search, Django/Celery/aiogram registration search, focused tests, seed failure rollback test, before/after query/latency measurement for any performance change. |
| **Rollback** | One small deletion/consolidation per commit with re-export shims where public imports existed; revert without data migration. |
| **Exit criterion** | Each removal has documented proof, no dynamic registration breaks, and retained layer names reflect actual responsibility. |

## Approval gates

| Gate | Required before |
|---|---|
| Product owner decision | B1 stale callbacks/global identity; B3 bonus timing, zero bill, revenue semantics, refund policy; B3.1/B3.2 decisions PQ-026–PQ-030 (approved 2026-10-23); B4 delivery guarantee/preference policy. |
| Data owner review | Any migration or constraint in B2/B3, after reporting invalid existing rows. |
| Operations review | B4 process topology, worker/beat/bot deployment, retry monitoring. |
| Code owner approval | Each batch’s implementation plan, test plan, rollback plan, and public compatibility impact. |

B3.2, B4 and B5 are complete. Next (owner-approved order,
`ECOSYSTEM_DIRECTION.md`): pickup ordering, renaming what people see to
Botera Shop, source tracking for deep links. Before production rollout of
B2–B5:
run the duplicate-data audits on a production copy and the full suite on
PostgreSQL.
