"""Fill a year of finished history for load partners lt-0000.. (SQL, fast).

usage: python gen_year.py PARTNERS ORDERS_PER_DAY [GUESTS_PER_PARTNER] [FIRST_PARTNER]
Per order: closed visit + session, completed order with 3 lines and 4 status
rows, paid bill (bill order, 3 bill lines, cash payment), cashback accrual,
2 staff notifications. Everything is in a final state, so it never shows up
as "open" work - it only makes tables and indexes big, as after a year.
"""

import sys
import time

import psycopg

P, PER_DAY = int(sys.argv[1]), int(sys.argv[2])
GUESTS = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
START = int(sys.argv[4]) if len(sys.argv) > 4 else 0
# Connection from env.sh (PGHOST, PGDATABASE, PGUSER, PGPASSWORD, PGPORT).
DSN = ""

SQL = """
create temp table g on commit drop as
select gen_random_uuid() as ta, gen_random_uuid() as gp, n
from generate_series(1, %(guests)s) n;

insert into users_telegramaccount (id, created_at, updated_at, telegram_id, username, first_name,
  last_name, language_code, is_blocked)
select ta, now() - interval '365 days', now(), 5000000000 + %(idx)s::bigint * 1000000 + n, '',
  'Гість', '',
  'uk', false from g;

insert into users_guestprofile (id, created_at, updated_at, customer_code, first_visit_at,
  last_visit_at,
  loyalty_balance, is_subscribed, partner_id, telegram_account_id, phone, delivery_address,
  shipping_carrier, shipping_destination, language)
select gp, now() - interval '365 days', now(), upper(substr(md5(gp::text), 1, 12)),
  now() - interval '365 days', now() - interval '1 day', 0, true, %(pid)s, ta, '', '', '', '', ''
from g;

create temp table o on commit drop as
select gen_random_uuid() as id, gen_random_uuid() as visit, gen_random_uuid() as sess,
  gen_random_uuid() as bill, t.id as table_id, t.number as tnum, g.gp as guest, x.ts
from (
  select d, k, 1 + floor(random() * %(guests)s)::int as gidx,
    date_trunc('day', now()) - (d || ' days')::interval + interval '9 hours'
      + (random() * interval '13 hours') as ts
  from generate_series(1, 365) d cross join generate_series(1, %(per_day)s) k
) x
join g on g.n = x.gidx
cross join lateral (select id, number from tables_table
                    where partner_id = %(pid)s and x.k > 0 order by random() limit 1) t;

insert into tables_tablevisit (id, created_at, updated_at, status, started_at, closed_at,
  partner_id, table_id)
select visit, ts, ts + interval '90 min', 'closed', ts, ts + interval '90 min', %(pid)s,
  table_id from o;

insert into tables_tablesession (id, created_at, updated_at, started_at, expires_at, closed_at,
  status,
  guest_id, partner_id, table_id, visit_id)
select sess, ts, ts + interval '90 min', ts, ts + interval '4 hours', ts + interval '90 min',
  'closed',
  guest, %(pid)s, table_id, visit from o;

create temp table li on commit drop as
select gen_random_uuid() as id, gen_random_uuid() as bill_line, o.id as order_id, o.bill, o.ts,
  m.id as menu_item, m.name, m.price
from o cross join lateral (
  select id, name, price from menu_menuitem
  where partner_id = %(pid)s and o.id is not null order by random() limit 3) m;

insert into orders_order (id, created_at, updated_at, public_id, status, payment_method, comment,
  subtotal_amount, bonus_spent, discount_amount, total_amount, accepted_at, received_at, paid_at,
  guest_id, partner_id, table_id, table_session_id, channel, delivery_address, payment_timing,
  shipping_carrier, shipping_cost, shipping_destination, tracking_number)
select o.id, ts, ts + interval '80 min', substr(md5(o.id::text), 1, 12), 'completed', 'cash', '',
  s.total, 0, 0, s.total, ts + interval '2 min', ts + interval '20 min', ts + interval '80 min',
  guest, %(pid)s, table_id, sess, 'table', '', '', '', '', '', ''
from o join (select order_id, sum(price) total from li group by order_id) s on s.order_id = o.id;

insert into orders_orderitem (id, created_at, updated_at, item_name, unit_price, quantity, comment,
  menu_item_id, order_id, partner_id, kind)
select id, ts, ts, name, price, 1, '', menu_item, order_id, %(pid)s, 'item' from li;

insert into orders_orderstatushistory (id, created_at, updated_at, from_status, to_status, note,
  order_id, partner_id)
select gen_random_uuid(), ts + (st.k || ' min')::interval, ts + (st.k || ' min')::interval,
  st.f, st.t, '',
  o.id, %(pid)s
from o cross join (values (0, '', 'new'), (2, 'new', 'accepted'), (15, 'accepted', 'ready'),
                          (20, 'ready', 'completed')) st(k, f, t);

insert into billing_bill (id, created_at, updated_at, public_id, status, kind, source, label,
  subtotal_amount, discount_amount, bonus_spent_amount, total_amount, paid_amount, external_ref,
  pos_tab_id, issued_at, closed_at, partner_id, primary_guest_id, table_id, visit_id,
    refunded_amount)
select bill, ts + interval '70 min', ts + interval '80 min', substr(md5(bill::text), 1, 12), 'paid',
  'personal', 'internal', 'Table #' || tnum, s.total, 0, 0, s.total, s.total, '', '',
  ts + interval '70 min', ts + interval '80 min', %(pid)s, guest, table_id, visit, 0
from o join (select order_id, sum(price) total from li group by order_id) s on s.order_id = o.id;

insert into billing_billorder (id, created_at, updated_at, bill_id, order_id, partner_id)
select gen_random_uuid(), ts + interval '70 min', ts + interval '70 min', bill, id, %(pid)s from o;

insert into billing_billitem (id, created_at, updated_at, item_name, unit_price, quantity,
  line_total,
  comment, bill_id, order_id, order_item_id, partner_id, is_active)
select bill_line, ts + interval '70 min', ts + interval '70 min', name, price, 1, price, '', bill,
  order_id, id, %(pid)s, true from li;

insert into billing_payment (id, created_at, updated_at, status, method, amount, provider_code,
  external_payment_id, comment, paid_at, bill_id, partner_id)
select gen_random_uuid(), ts + interval '80 min', ts + interval '80 min', 'paid', 'cash',
  s.total, '', '',
  '', ts + interval '80 min', bill, %(pid)s
from o join (select order_id, sum(price) total from li group by order_id) s on s.order_id = o.id;

insert into bonuses_bonustransaction (id, created_at, updated_at, transaction_type, amount, comment,
  guest_id, order_id, partner_id, program_id, idempotency_key, source_id, source_type)
select gen_random_uuid(), ts + interval '80 min', ts + interval '80 min', 'accrual', 5, '',
  guest, o.id,
  %(pid)s, (select id from bonuses_bonusprogram where partner_id = %(pid)s limit 1),
  'year:' || o.id, o.id::text, 'order_paid'
from o;

insert into notifications_staffnotification (id, created_at, updated_at, category, title, message,
  is_read, delivery_status, delivered_at, delivery_error, employee_id, order_id, partner_id,
    dedupe_key)
select gen_random_uuid(), ts + (c.k || ' min')::interval, ts, c.cat, 'Нове замовлення', 'Стіл '
  || tnum,
  true, 'sent', ts, '', (select id from employees_employeeprofile where partner_id = %(pid)s
    limit 1),
  o.id, %(pid)s, ''
from o cross join (values (0, 'order_created'), (2, 'order_status_changed')) c(k, cat);
"""

with psycopg.connect(DSN, autocommit=False, cursor_factory=psycopg.ClientCursor) as conn:
    partners = conn.execute(
        "select id, slug from partners_partner where slug like 'lt-%%' "
        "order by slug limit %s offset %s",
        (P, START),
    ).fetchall()
    conn.commit()
    total_start = time.monotonic()
    for idx, (pid, slug) in enumerate(partners, start=START):
        start = time.monotonic()
        with conn.transaction():
            conn.execute(SQL, {"pid": pid, "idx": idx, "per_day": PER_DAY, "guests": GUESTS},
                         prepare=False)
        print(f"{slug}: {time.monotonic() - start:.1f}s", flush=True)
    conn.autocommit = True
    conn.execute("analyze")
    size = conn.execute("select pg_size_pretty(pg_database_size('loadtest'))").fetchone()[0]
    print(f"done in {time.monotonic() - total_start:.0f}s, database {size}")
