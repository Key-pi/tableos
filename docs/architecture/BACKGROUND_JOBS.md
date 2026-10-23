# TableOS — Background jobs and external delivery

**Status:** current registrations and target reliability contract.

## Registered task inventory

Celery autodiscovery currently registers these first-party tasks:

| Task | Source | Queue | Trigger | Retry | Idempotency today |
|---|---|---|---|---|---|
| `notifications.deliver_staff_notification` | `apps/notifications/tasks.py` | `default` | `on_commit` after a `StaffNotification` row is created | `autoretry_for=Exception`, backoff, max 5 | none: does not skip an already `sent` row; recipient not rechecked (AA-030) |
| `notifications.send_guest_order_status_update` | `apps/notifications/tasks.py` | `default` | `on_commit` after an order status change, if guest allows updates | same | none (AA-030) |
| `notifications.send_broadcast_campaign` | `apps/notifications/tasks.py` | `broadcasts` | `on_commit` from `send_broadcast_campaign`; chains itself per batch of 250 | same | none; Telegram I/O inside the DB transaction (AA-008) |
| `notifications.process_scheduled_broadcast_campaigns` | `apps/notifications/tasks.py` | `default` | beat, every minute | none | relies on campaign status; no row lock |
| `analytics.refresh_daily_partner_metrics` | `apps/analytics/tasks.py` | `default` | beat, every 15 minutes | none | `update_or_create` per partner and day; only the current day (AA-029) |

Telegram API errors inside `_send_telegram_messages` are captured as failed
results and are not re-raised, so the retry policy applies only to unexpected
exceptions (database errors, decryption errors and similar).

The legacy cheatsheet omitted scheduled campaign processing; this list is the
source-verified registration map. Placeholder `tasks.py` files in other apps
do not demonstrate registered background work.

## Current behavior

Many ordinary order notification enqueue operations use `transaction.on_commit`,
which is the right direction: business data commits before asynchronous work is
scheduled. The broadcast workflow is an exception: its service sends Telegram
messages/sleeps within an atomic transaction and the task lacks a durable
recipient claim/lease/idempotency record. See AA-008 for exact evidence.

Deployment documentation must distinguish the processes actually required:
web/admin, bot polling, Celery worker(s), Celery beat, Redis and PostgreSQL.
`docker-compose.yml` declares only `db`, `redis`, `web` and `worker`; without
separately started `runbot` and `celery beat` processes nothing polls
Telegram, scheduled campaigns are never sent and daily metrics are never
refreshed.

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

No implementation should introduce a generic event bus or outbox product until
the narrow broadcast/notification requirements justify it. A durable recipient
delivery record is the first evidence-backed design candidate, subject to the
owner’s PQ-014/PQ-015 decisions.

## Operational checks

- Verify all task names after any module move; Celery autodiscovery is a dynamic
  consumer.
- Verify broker/backend settings and worker queue routing in the actual
  deployment environment.
- Record scheduler ownership and cadence for scheduled broadcasts.
- Alert on repeat failure, stranded lease, and campaign counter mismatch.
- Never call Telegram, retry sleep, or long fan-out loop inside a source DB
  transaction.

## Test matrix

1. enqueue only after commit;
2. one recipient/campaign claimed by two workers;
3. task retry after transient provider failure;
4. crash after provider send but before result persistence;
5. disabled/preference-ineligible recipient at delivery time;
6. scheduled campaign is discovered only once per intended schedule;
7. order state persistence is not rolled back because notification delivery
   fails.
