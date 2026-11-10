"""How a record names itself (``__str__``): with its venue for the platform,
without it inside one venue's Admin (admin step 4, PQ-050).

The platform works with every venue at once, so a record reads
«Night Owl · Поділ / order 3b65ae44». The owner and staff of a venue see one
venue: there the same prefix on every row, select and page title is noise.
``core/middleware.py:VenueLabelsMiddleware`` switches the short form on for
the Admin requests of anyone who is not the platform; everywhere else
(the bot, tasks, logs) labels stay full.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_one_venue: ContextVar[bool] = ContextVar("labels_within_one_venue", default=False)


def within_one_venue() -> bool:
    return _one_venue.get()


@contextmanager
def one_venue():
    token = _one_venue.set(True)
    try:
        yield
    finally:
        _one_venue.reset(token)


def venue_label(record, rest) -> str:
    """``rest`` inside a venue's Admin, «<venue> / rest» elsewhere."""

    if _one_venue.get():
        return str(rest)
    return f"{record.partner.name} / {rest}"
