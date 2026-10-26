"""SQL queries and time of Admin pages for one load-test owner.

usage: python page_queries.py admin_sessions.json [PATH ...]
Each page is opened once to warm up, then measured.
"""

import json
import sys
import time

import django

django.setup()

from django.db import connection  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

DEFAULT_PAGES = [
    "/admin/billing/bill/",
    "/admin/orders/order/",
    "/admin/",
    "/admin/users/guestprofile/",
]

session = json.load(open(sys.argv[1]))[0]
client = Client(HTTP_HOST="shop.example.com", HTTP_X_FORWARDED_PROTO="https")
client.cookies["sessionid"] = session["sessionid"]
pages = sys.argv[2:] or DEFAULT_PAGES + session["detail_paths"][:1]
for path in pages:
    client.get(path, secure=True)
    with CaptureQueriesContext(connection) as queries:
        started = time.monotonic()
        response = client.get(path, secure=True)
        elapsed = time.monotonic() - started
    sql_ms = sum(float(query["time"]) for query in queries.captured_queries) * 1000
    print(
        f"{path:45} status={response.status_code} queries={len(queries)} "
        f"sql={sql_ms:.0f}ms total={elapsed * 1000:.0f}ms"
    )
