"""The venue switch in the Admin header (PQ-047, stage 1).

Shown only to someone who may open more than one venue of a network.
Unfold reads ``site_dropdown`` and ``site_subheader`` from ``UNFOLD`` in
settings; ``switch_admin_point`` stores the choice in the session and
``core/admin_mixins.py:current_admin_partner_id`` re-checks it on every
request.
"""

from __future__ import annotations

from uuid import UUID

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpRequest, HttpResponseRedirect
from django.urls import reverse

from apps.partners.models import Partner
from apps.partners.networks import admin_partner_ids
from core.admin_mixins import ADMIN_POINT_SESSION_KEY, current_admin_partner_id


def _points(request) -> list[Partner]:
    if not getattr(request, "user", None) or request.user.is_superuser:
        return []
    ids = admin_partner_ids(request.user)
    if len(ids) < 2:
        return []
    by_id = {partner.id: partner for partner in Partner.objects.filter(id__in=ids)}
    return [by_id[point] for point in ids if point in by_id]


def site_dropdown(request) -> list[dict]:
    points = _points(request)
    if not points:
        return []
    current = current_admin_partner_id(request)
    return [
        {
            "title": f"✓ {point.name}" if point.id == current else point.name,
            "link": reverse("admin-point", args=[point.id.hex]),
            "icon": "storefront",
        }
        for point in points
    ]


def header_badge(request):
    """The open venue as a label in the Admin header, so nobody edits the
    wrong one (networks only). Unfold's ``ENVIRONMENT``: [text, kind]."""

    name = site_subheader(request)
    return [f"📍 {name}", "info"] if name else None


def site_subheader(request) -> str:
    """The venue now open in Admin, under the site name (networks only)."""

    points = _points(request)
    current = current_admin_partner_id(request) if points else None
    return next((point.name for point in points if point.id == current), "")


@staff_member_required
def switch_admin_point(request: HttpRequest, partner_hex: str) -> HttpResponseRedirect:
    """Open another venue of the network. Only changes what this person's
    own Admin shows, and only to a venue they may open."""

    try:
        partner_id = UUID(hex=partner_hex)
    except ValueError as exc:
        raise Http404 from exc
    if partner_id not in admin_partner_ids(request.user):
        raise Http404
    request.session[ADMIN_POINT_SESSION_KEY] = str(partner_id)
    # A page of the old venue's object would answer 404: start at the index.
    return HttpResponseRedirect(reverse("admin:index"))
