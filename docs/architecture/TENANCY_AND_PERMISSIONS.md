# TableOS — Tenancy and permissions

**Status:** audited current controls and target invariants. This document does
not claim that the target controls are already implemented.

## Trust model

| Context | Trusted input | Must be derived/validated | Must never be trusted by itself |
|---|---|---|---|
| Telegram guest update | active bot instance/runtime partner | Telegram identity → guest profile; table/session/order belong to that partner | callback payload ID, reply text, a user-supplied partner ID |
| Telegram staff update | active bot/runtime partner and Telegram identity | active `User`, active `EmployeeProfile`, profile/user partner consistency, current capability | previously rendered keyboard/card, employee ID in callback |
| Django Admin | authenticated account plus Admin access profile | scoped queryset, section/object policy, related-field/inline partner ownership, approved command | `is_staff` alone, list-filter choice, submitted foreign key |
| Celery task | durable source record/command ID | current source partner/status/recipient eligibility and idempotency claim | task payload as current authorization |
| Service/management caller | explicit trusted actor and partner context | every foreign object relation shares the partner | bare IDs or an object from an arbitrary queryset |

## Current enforcement and audited gaps

| Control | Confirmed current behavior | Gap / evidence | Status |
|---|---|---|---|
| Partner-scoped bot reads | Main guest/staff handlers resolve partner from bot runtime and often use scoped selectors | Public service APIs are not uniformly guarded against mismatched object graphs | PARTIAL_CURRENT |
| Partner-bound model | `PartnerBoundModel` supplies `partner` FK | FK alone cannot enforce same partner across linked tables (`core/database/models.py:22-33`) | DEFECT — AA-002 |
| Staff eligibility | Employee selector checks active profile | It omits `User.is_active` (`apps/employees/selectors.py:11-16`) | DEFECT — AA-001 |
| Runtime suspension | Startup validates partner state | Per-update context does not recheck status (`bot/services/context.py:5-7`) | PARTIAL_CURRENT — AA-009 |
| Admin object scope | `ScopedAdminMixin` limits normal object querysets | partner list filters, inlines and global identity form need further scope/authority guards | PARTIAL_CURRENT — AA-015 |
| Admin access vs staff role | `AdminAccessProfile` gates back office | it is not the same as an active staff profile/capability | DESIGN RULE |

## Target invariants

1. Every partner-bound write validates one common `Partner` for actor, target,
   and related objects before mutation.
2. A protected staff command requires all of: active Django user, active
   employee profile, matching partner, and capability at command time.
3. A suspended partner has explicitly approved behavior at every update and
   task boundary; the default safe interpretation is deny mutation.
4. Global Telegram identity has an explicit owner/change policy and cannot be
   silently modified through a tenant-local staff form.
5. Admin list filters, foreign keys, inlines, actions, and forms provide no
   weaker tenant boundary than normal change-list querysets.
6. Cross-table partner identity is enforced in services now and, after data
   audit, with the narrowest viable PostgreSQL schema mechanism.

## Implementation guard pattern

An owner service should receive an actor/context with a resolved partner, load
the canonical target under that partner scope, and verify every related object
before starting a state/money mutation. A generic helper may remove repetition,
but it must not obscure the owner service’s authorization policy.

```text
trusted update/admin context
  → resolve active partner
  → resolve active actor + capability
  → load target scoped by partner
  → validate all linked objects share partner
  → acquire command locks and mutate
```

This is deliberately not a claim that Django `clean()` or a UI choice is a
security boundary. Database constraints should be selected only after a data
audit and an approved migration strategy; ordinary `CheckConstraint` cannot
compare values from related rows.

## Verification matrix

| Scenario | Required test level |
|---|---|
| User disabled after employee profile exists | bot handler and owner service |
| Staff capability removed after card is displayed | callback/owner service |
| Partner suspended while polling process is running | runtime context and protected handler |
| Foreign guest/table/session/menu object supplied to command | service test, two tenants |
| Tenant admin list filters / related fields / inlines | Django Admin request/form test |
| Shared Telegram identity changed by tenant admin | form/service two-tenant test |
| Existing invalid tenant graph before constraint migration | data audit query and migration dry-run |

Open semantics are tracked in
[`../product/OPEN_PRODUCT_QUESTIONS.md`](../product/OPEN_PRODUCT_QUESTIONS.md),
especially PQ-013, PQ-019 and PQ-020.
