# TableOS — Refactor roadmap

**Status:** B0 and B1 are complete; B2 was owner-approved on 2026-08-06 and its
implementation update is recorded below. B3–B5 remain proposals and require
owner approval before any application-code, migration, dependency, or runtime
change begins.

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

**Required owner decisions before implementation:**

1. Is order bonus earned at `completed`, `paid`, or another terminal event?
2. What is the policy for refund/cancellation after earned/redeemed bonuses?
3. Is a 100% bonus redemption allowed, and how does a zero-total bill settle?
4. Which dashboard labels/values represent gross sales, net cash/payment revenue,
   and bonus discount?

## Batch B4 — Durable async delivery and operational runtime

| Field | Proposal |
|---|---|
| **Goal** | Make notification/broadcast delivery recoverable without database transactions spanning Telegram I/O. |
| **Scope** | Separate commit from send; introduce the smallest durable delivery/lease/idempotency record necessary for campaign fan-out; align exception/retry policy; recheck recipient eligibility as product requires; document/deploy actual bot/worker/scheduler topology. |
| **Not in scope** | A generic event bus, message broker replacement, microservice extraction, unmeasured caching. |
| **Evidence / risks** | AA-008, AA-009, AA-016, AA-018. |
| **Dependencies** | B1 live eligibility semantics; owner decision on delivery guarantee and recipient preference behavior; operational deployment owner. |
| **Required verification** | Worker retry, duplicate task, concurrent worker, crash-after-send/before-persist, disabled recipient, and campaign counter tests; instrumentation showing no network I/O in the source transaction; compose/runbook review. |
| **Rollback** | Retain original campaign records and make the new delivery worker opt-in per campaign version until recovery behavior is proven; migration must preserve unsent work. |
| **Exit criterion** | Delivery attempt has durable identity/lease/outcome, retries are intentional, and operational documentation names every required process. |

## Batch B5 — Evidence-backed consolidation and operational hygiene

| Field | Proposal |
|---|---|
| **Goal** | Remove only proven-unused scaffolding and tighten lower-risk reliability/performance issues after behavior is protected. |
| **Scope** | Resolve empty services/tasks/interfaces/repositories through static + dynamic consumer proof; break notifications task/service cycle; make demo seed atomic/idempotent; measure and address confirmed filter/report performance issues. |
| **Not in scope** | Broad folder renames, code formatting sweep, dependency upgrade, speculative cache/event-engine abstraction. |
| **Evidence / risks** | AA-017, AA-019 and lower-priority observations in architecture audit. |
| **Dependencies** | Characterization coverage from B1–B4; public import compatibility plan (ADR-0005). |
| **Required verification** | Import/API search, Django/Celery/aiogram registration search, focused tests, seed failure rollback test, before/after query/latency measurement for any performance change. |
| **Rollback** | One small deletion/consolidation per commit with re-export shims where public imports existed; revert without data migration. |
| **Exit criterion** | Each removal has documented proof, no dynamic registration breaks, and retained layer names reflect actual responsibility. |

## Approval gates

| Gate | Required before |
|---|---|
| Product owner decision | B1 stale callbacks/global identity; B3 bonus timing, zero bill, revenue semantics, refund policy; B4 delivery guarantee/preference policy. |
| Data owner review | Any migration or constraint in B2/B3, after reporting invalid existing rows. |
| Operations review | B4 process topology, worker/beat/bot deployment, retry monitoring. |
| Code owner approval | Each batch’s implementation plan, test plan, rollback plan, and public compatibility impact. |

The next safe action is to resolve the listed B3 product/data decisions before
starting financial-integrity implementation.
