"""Traffic sources: who brought a guest to the bot (`/start src_<code>`).

Owner decisions of 2026-10-23: a guest is attributed to the source of the
/start that created their profile at the venue (first touch); every click
through a source link is logged too. Sources are created by the venue in
Admin; an unknown or switched-off code is ignored and the guest gets the
normal welcome.
"""

from __future__ import annotations

import re

from django.db import IntegrityError, transaction

from apps.analytics.models import SourceVisit, TrafficSource
from apps.users.models import GuestProfile

_CODE_RE = re.compile(TrafficSource.CODE_PATTERN)


def parse_source_payload(payload: str) -> str | None:
    """The source code of a `/start` payload.

    ``None`` — not a source link (for example a table QR); ``""`` — a source
    link whose code is malformed (it must not be treated as a table either).
    """

    payload = (payload or "").strip()
    if not payload.startswith(TrafficSource.PAYLOAD_PREFIX):
        return None
    code = payload[len(TrafficSource.PAYLOAD_PREFIX) :].lower()
    return code if _CODE_RE.fullmatch(code) else ""


def record_source_visit(
    *, partner_id, guest_id, payload: str, is_new_guest: bool
) -> SourceVisit | None:
    """Log a /start through a source link of this partner; ``None`` if it is not one.

    ``is_new_guest`` comes from the caller that just created the guest profile.
    The partial unique constraint keeps one acquiring visit per guest: a second
    claim is stored as an ordinary click.
    """

    code = parse_source_payload(payload)
    if not code:
        return None
    source = TrafficSource.objects.filter(partner_id=partner_id, code=code, is_active=True).first()
    if source is None:
        return None
    guest = GuestProfile.objects.get(partner_id=partner_id, id=guest_id)

    if is_new_guest:
        try:
            with transaction.atomic():
                return SourceVisit.objects.create(
                    partner_id=partner_id, source=source, guest=guest, is_new_guest=True
                )
        except IntegrityError:
            pass
    return SourceVisit.objects.create(
        partner_id=partner_id, source=source, guest=guest, is_new_guest=False
    )
