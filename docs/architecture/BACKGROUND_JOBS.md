# TableOS — Background jobs and external delivery

**Status:** current registrations and target reliability contract.

## Registered task inventory

Celery autodiscovery currently registers these first-party tasks:

| Task | Source | Queue | Trigger | Retry | Idempotency today |
|---|---|---|---|---|---|
| `notifications.deliver_staff_notification` | `apps/notifications/tasks.py` | `default` | `on_commit` after a `StaffNotification` row is created | `autoretry_for=Exception`, backoff, max 5; `acks_late`, `reject_on_worker_lost` | skips a row already `sent`; rechecks active employee/user and opt-in; transient Telegram errors raise `RetryableDeliveryError` (at least once, PQ-015) |
| `notifications.send_guest_order_status_update` | `apps/notifications/tasks.py` | `default` | `on_commit` after an order status change, if guest allows updates | same | rechecks the preference; only a transient failure raises for retry, nothing raises after a successful send |
| `notifications.send_broadcast_campaign` | `apps/notifications/tasks.py` | `broadcasts` | `on_commit` from `send_broadcast_campaign`; queues itself per batch | same | `BroadcastDelivery` rows: claim `pending → sending` (skip-locked), send outside transactions, record each outcome; never re-claims `sending` (at most once, PQ-014) |
| `notifications.process_scheduled_broadcast_campaigns` | `apps/notifications/tasks.py` | `default` | beat, every minute | none | `send_broadcast_campaign` locks the campaign row and ignores queued/sending/sent campaigns |
| `notifications.recover_stalled_broadcast_campaigns` | `apps/notifications/tasks.py` | `default` | beat, every 5 minutes | none | expired claims (30 min) → `unknown`; re-queues sending campaigns with pending rows and no recent claim; finalizes finished ones |
| `analytics.refresh_daily_partner_metrics` | `apps/analytics/tasks.py` | `default` | beat, every 15 minutes | none | `update_or_create` per partner and day; current day, plus the previous day once after the venue's local midnight |

`_send_telegram_messages` classifies each result: sent; definite failure
(`Forbidden` also marks the account blocked, `BadRequest`); or ambiguous
(network/server error, unexpected exception), where Telegram may have
delivered the message. Operational notifications retry ambiguous failures;
broadcasts record them as `unknown` and never resend (ADR-0008).

The legacy cheatsheet omitted scheduled campaign processing; this list is the
source-verified registration map. Placeholder `tasks.py` files in other apps
do not demonstrate registered background work.

## Current behavior (since B4)

Business data commits before asynchronous work is scheduled
(`transaction.on_commit`), and no Telegram call runs inside a database
transaction. A broadcast records its audience as `BroadcastDelivery` rows
when sending starts; campaign counters (`delivered_count`, `failed_count`,
`unknown_count`) are derived from those rows. Re-queueing a campaign reaches
only guests not attempted yet. Tasks queued by the pre-B4 cursor-based sender
resume after their cursor; a pre-B4 campaign that is mid-way with no cursor is
marked failed instead of being resent.

Required processes, all declared in `docker-compose.yml`: `web`, `worker`
(queues `default` and `broadcasts`), exactly one `beat`, exactly one `bot`
(`manage.py runbot`), PostgreSQL and Redis. Without beat, scheduled campaigns,
stalled-broadcast recovery and daily metrics do not run.

## Target task contract

| Step | Required behavior |
|---|---|
| Persist intent | Source service stores/commits durable work or a versioned job reference. |
| Schedule | `on_commit` enqueues only after durable state exists. |
| Claim | Task claims a campaign/recipient unit with a lease/idempotency key in a short transaction. |
| Deliver | Telegram/network I/O occurs outside that transaction. |
| Record | A short follow-up transaction records attempt, outcome, error and counters. |
| Retry | Retry policy is based on an observable failure and does not recreate a completed recipient delivery. |
| Recover | Crash/retry/parallel workers converge according to an approved delivery guarantee. |

B4 implements this contract with a per-recipient delivery record only; no
generic event bus or outbox product was introduced (ADR-0008).

## Operational checks

- Verify all task names after any module move; Celery autodiscovery is a dynamic
  consumer.
- Verify broker/backend settings and worker queue routing in the actual
  deployment environment.
- Record scheduler ownership and cadence for scheduled broadcasts.
- Alert on repeat failure, a growing `unknown_count`, and campaigns that stay
  `sending` (recovery should finalize or resume them within minutes).
- Never call Telegram, retry sleep, or long fan-out loop inside a source DB
  transaction.

## Test matrix

Covered by `apps/notifications/test_delivery.py` (B4); item 2 runs on
PostgreSQL only.

1. enqueue only after commit;
2. one recipient/campaign claimed by two workers;
3. task retry after transient provider failure;
4. crash after provider send but before result persistence;
5. disabled/preference-ineligible recipient at delivery time;
6. scheduled campaign is discovered only once per intended schedule;
7. order state persistence is not rolled back because notification delivery
   fails.
