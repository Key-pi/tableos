"""What of the menu belongs to Poster now (PQ-058, step 3).

The menu's Admin asks ``poster_fields`` for a row and shows those fields
read only: the venue works with Poster and the row came from it. Disconnected
— everything is Botera's again. The list follows ``menu_sync`` — what it
writes; the stop list («В наличии» of an item, «Показывать» of a category) is
not in it: Botera may hide what Poster sells (``PosterRef.hidden_in_poster``).
"""

from __future__ import annotations

from apps.integrations.poster.models import PosterConnection, PosterRef

POSTER_FIELDS = {
    "menu.menucategory": ("name", "parent"),
    "menu.menuitem": ("category", "name", "price", "image_url"),
    "menu.menuitemvariant": ("name", "price", "is_available"),
    "menu.modifiergroup": ("name", "min_choices", "max_choices", "is_active", "items",
                           "categories"),
    "menu.modifier": ("name", "price", "is_available"),
}
KINDS = {
    "menu.menucategory": PosterRef.Kind.CATEGORY,
    "menu.menuitem": PosterRef.Kind.PRODUCT,
    "menu.menuitemvariant": PosterRef.Kind.VARIANT,
    "menu.modifiergroup": PosterRef.Kind.MODIFIER_GROUP,
    "menu.modifier": PosterRef.Kind.MODIFIER,
}
_CACHE = "_poster_fields"


def poster_menu_on(partner_id) -> bool:
    return PosterConnection.objects.filter(
        partner_id=partner_id, status=PosterConnection.Status.ACTIVE,
    ).exists()


def poster_fields(obj) -> tuple[str, ...]:
    """The fields of this catalog row that Poster owns now (none for a new
    row, a row of Botera's own or a venue without Poster)."""

    if obj is None or getattr(obj, "pk", None) is None:
        return ()
    label = obj._meta.label_lower
    if label not in KINDS:
        return ()
    if _CACHE not in obj.__dict__:
        linked = poster_menu_on(obj.partner_id) and PosterRef.objects.filter(
            partner_id=obj.partner_id, kind=KINDS[label], object_id=obj.pk,
        ).exists()
        obj.__dict__[_CACHE] = POSTER_FIELDS[label] if linked else ()
    return obj.__dict__[_CACHE]
