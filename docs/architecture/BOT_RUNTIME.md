# TableOS — Bot runtime and handler boundary

**Status:** current runtime map plus target guardrails.

## Current topology

The aiogram polling process starts from `bot/main.py`. `bot/dispatcher.py`
includes routers in the order `start → session → menu → order → profile →
billing → guest_call → staff`. Aiogram resolves the first matching handler, so
router order and text filters are observable behavior.

| Concern | Current implementation | Governance rule |
|---|---|---|
| Partner resolution | bot token/runtime selects partner | per-update context must revalidate active/suspension policy |
| Router assembly | static inclusion in `bot/dispatcher.py` | new router registration is a dynamic-consumer check in review |
| Reply labels | exact configured text filters in `bot/filters/content.py` | actionable labels must be collision-safe or use stable callback intent |
| Callback data | handler resolves domain record/employee | callback identifier is not authorization; reload scoped current state |
| ORM access | handlers generally wrap Django access with `sync_to_async` | retain non-blocking adapter behavior; do not put business invariants in handler |
| Runtime cache | partner bot runtime built at startup | cache cannot be sole source for revocable status/capability |

## Handler contract

Each handler should do only the following:

1. parse/validate the Telegram update and known callback schema;
2. resolve current partner and current actor/guest context;
3. call one owner service or selector with scoped IDs/context;
4. translate a typed result or controlled domain error into Telegram UI.

Handlers must not mutate ORM models directly, decide a cross-module money rule,
or rely on a keyboard created earlier as evidence of permission. Keyboard
rendering may hide unavailable operations, but execution must perform the same
live guard again.

## Current exception cases recorded by audit

- Staff eligibility misses `User.is_active`; see AA-001.
- Runtime suspension is startup-only rather than a confirmed per-update guard;
  see AA-009.
- Several staff callbacks resolve an employee but do not demonstrate a renewed
  module/capability check; stale-card product semantics remain PQ-004/PQ-013.
- Duplicate configurable labels can route based on handler order; see AA-010
  and PQ-016.

## Callback and content design

Use a namespaced callback/action key plus a scoped object identifier. The
service must independently reload/authorize it. If a reply-text action is
retained for UX reasons, configuration validation must prove that it maps to
exactly one enabled intent in the given context. Renaming a label must not
quietly convert a saved/stale user message into another command.

## Runtime verification

Tests and manual review for a bot-affecting change must cover:

- router inclusion/order and the intended first match;
- current active partner and suspended-partner outcome;
- inactive user/profile and partner mismatch;
- callback replay and permission removal after UI render;
- `sync_to_async` boundary for ORM/service work;
- changed callback schema compatibility;
- no hardcoded routing text conflicting with partner configuration.

Tasks are documented separately in
[`BACKGROUND_JOBS.md`](BACKGROUND_JOBS.md); they are adapters too and must not
be used as a way to bypass the owner service.
