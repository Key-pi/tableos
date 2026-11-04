"""Networks of venues (PQ-047, stage 1): which venues one Admin login reaches.

A venue stays a partner: data, rights and locks keep working per venue.
The network adds only the list of venues a person may switch between in
Admin and act for:

- the owner of a network (``AdminAccessProfile.is_partner_owner`` of a venue
  in it) — every venue of that network;
- anyone else — their own venue plus ``AdminAccessProfile.extra_partners``
  that are in the same network (a venue moved to another network stops
  counting at once);
- a venue without a network — only itself.
"""

from __future__ import annotations

from apps.partners.models import Partner
from apps.users.models import AdminAccessProfile


def _active_admin_profile(user) -> AdminAccessProfile | None:
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    return (
        AdminAccessProfile.objects.select_related("partner")
        .filter(user=user, is_active=True, can_access_admin=True)
        .first()
    )


def admin_partner_ids(user) -> list:
    """Venues this person may open in Admin, their own venue first."""

    profile = _active_admin_profile(user)
    if profile is None:
        return []
    home = profile.partner
    if home.network_id is None:
        return [home.id]
    others = Partner.objects.filter(network_id=home.network_id).exclude(id=home.id)
    if not profile.is_partner_owner:
        others = others.filter(id__in=profile.extra_partners.values("id"))
    return [home.id, *others.order_by("name").values_list("id", flat=True)]


def acts_for_partner(user, partner_id) -> bool:
    """Whether a back-office actor may act for this venue: its own staff, or
    the network's owner/manager who was given it (PQ-047)."""

    if user is None or not getattr(user, "is_active", False):
        return False
    if user.partner_id is not None and str(user.partner_id) == str(partner_id):
        return True
    return any(str(point) == str(partner_id) for point in admin_partner_ids(user))


def owns_partner(user, partner_id) -> bool:
    """The owner of this venue or of the network it is in."""

    profile = _active_admin_profile(user)
    if profile is None or not profile.is_partner_owner:
        return False
    return any(str(point) == str(partner_id) for point in admin_partner_ids(user))
