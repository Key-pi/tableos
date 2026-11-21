"""What of the menu belongs to Poster now (PQ-058, step 3), and what the
register takes (step 7).

The menu's Admin asks ``poster_fields`` for a row and shows those fields
read only: the venue works with Poster and the row came from it. Disconnected
— everything is Botera's again. The list follows ``menu_sync`` — what it
writes; the stop list («В наличии» of an item, «Показывать» of a category) is
not in it: Botera may hide what Poster sells (``PosterRef.hidden_in_poster``).

Step 7: the menu, the cart and checkout read here what the register takes —
pickup and delivery while Poster works (``register_takes``), and whether
Botera's own items and add-ons are hidden there (``own_menu_hidden``, the
venue's «Меню в боте»). Only Poster's own tables are read: no request to
Poster, nothing of billing or orders is imported.
"""

from __future__ import annotations

from django.db.models import Exists, OuterRef

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
# What goes to the register in v1 (owner 2026-11-20): pickup and delivery,
# ``orders.py:SERVICE_MODES``.
REGISTER_CHANNELS = frozenset({"pickup", "delivery"})


def _working(partner_id):
    return PosterConnection.objects.filter(
        partner_id=partner_id, status=PosterConnection.Status.ACTIVE,
    )


def poster_menu_on(partner_id) -> bool:
    return _working(partner_id).exists()


def register_takes(partner_id, channel) -> bool:
    """Orders of this channel go to the venue's Poster: paid on receipt, no
    bonuses (v1)."""

    return channel in REGISTER_CHANNELS and _working(partner_id).exists()


def own_menu_hidden(partner_id, channel) -> bool:
    """«Только меню Poster»: Botera's own items and add-ons are not sold in
    this channel — the register would not take them."""

    return channel in REGISTER_CHANNELS and _working(partner_id).filter(
        menu_mode=PosterConnection.MenuMode.POSTER,
    ).exists()


def linked_ids(partner_id, model, object_ids) -> set:
    """Which of these rows of ``model`` came from Poster (removed there or not,
    as ``orders.py`` sends them)."""

    return set(PosterRef.objects.filter(
        partner_id=partner_id, kind=KINDS[model._meta.label_lower],
        object_id__in=list(object_ids),
    ).values_list("object_id", flat=True))


def only_linked(queryset):
    """The rows of the queryset that came from Poster."""

    kind = KINDS[queryset.model._meta.label_lower]
    return queryset.filter(Exists(PosterRef.objects.filter(
        partner_id=OuterRef("partner_id"), kind=kind, object_id=OuterRef("pk"),
    )))


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


def with_poster_mark(queryset):
    """The rows annotated ``from_poster`` — linked to Poster (removed there
    or not): the menu's lists show which rows go to the register."""

    kind = KINDS[queryset.model._meta.label_lower]
    return queryset.annotate(from_poster=Exists(PosterRef.objects.filter(
        partner_id=OuterRef("partner_id"), kind=kind, object_id=OuterRef("pk"),
    )))
