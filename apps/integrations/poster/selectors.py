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
venue's «Меню в боте»); the «Новый заказ» message — whether the order will go
to the register (``goes_to_register``); staff screens and services — whether
the register holds an order (``register_holds``): then accepting,
cancelling, the money and completing are the register's (owner 2026-11-21),
and the transfer of a prepaid order is confirmed there (v2, PQ-061).
Only Poster's own tables (and the order's lines) are read: no request to
Poster, nothing of billing's or orders' services is imported.
"""

from __future__ import annotations

import uuid

from django.db.models import Exists, OuterRef

from apps.integrations.poster.models import PosterConnection, PosterOrderPush, PosterRef

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
    """Orders of this channel go to the venue's Poster: no bonuses; a
    transfer in advance goes there paid (v2, PQ-061)."""

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


def goes_to_register(order) -> bool:
    """The order will be sent to the register: its channel goes there and
    every line, variant and add-on came from Poster (what ``orders.py``
    sends; bonuses are not offered there since step 7)."""

    if not register_takes(order.partner_id, order.channel):
        return False
    from apps.menu.models import MenuItem, MenuItemVariant, Modifier
    from apps.orders.models import OrderItem

    lines = [line for line in order.items.all() if line.kind != OrderItem.Kind.DELIVERY_FEE]
    if not lines:
        return False
    items = {line.menu_item_id for line in lines}
    variants = {line.variant_id for line in lines if line.variant_id}
    modifiers = {_hex(row.get("id")) for line in lines for row in line.modifiers or []}
    return (
        linked_ids(order.partner_id, MenuItem, items) == items
        and linked_ids(order.partner_id, MenuItemVariant, variants) == variants
        and {value.hex for value in linked_ids(order.partner_id, Modifier, modifiers - {""})}
        == modifiers
    )


def _hex(value) -> str:
    try:
        return uuid.UUID(str(value)).hex
    except (TypeError, ValueError):
        return ""


def register_holds(order) -> bool:
    """The register holds the order now: it went (or goes) to the venue's
    working Poster — accepting, cancelling, the money and completing are done
    there; Botera's staff only say «Готово» and «В пути». A push that failed,
    Poster disconnected, or the register done with it — its check deleted or
    rejected (PQ-061) — the order is Botera's again."""

    if HELD in order.__dict__:  # ``with_register_mark``
        return bool(order.__dict__[HELD])
    if getattr(order, "pk", None) is None:  # not saved (or a stand-in)
        return False
    return _held_pushes().filter(order_id=order.pk, partner_id=order.partner_id).exists()


def sent_to_register(order) -> bool:
    """The register got the order (its push was sent), whether it holds it
    still or not: a prepaid order canceled then was the register's call
    (v2, PQ-061) — the guest is told the venue returns the money."""

    return PosterOrderPush.objects.filter(
        order_id=order.pk, partner_id=order.partner_id, status=PosterOrderPush.Status.SENT,
    ).exists()


def register_holds_any(order_ids) -> bool:
    """``register_holds`` for any of these orders (a bill's orders)."""

    ids = [order_id for order_id in order_ids if order_id]
    return bool(ids) and _held_pushes().filter(order_id__in=ids).exists()


HELD = "poster_held"
# What the register says when it is done with an order (``statuses.py``): the
# check deleted or rejected, the online order rejected.
CHECK_REMOVED, CHECK_REJECTED, ONLINE_REJECTED = 3, 4, 7


def _held_pushes():
    return PosterOrderPush.objects.exclude(status=PosterOrderPush.Status.FAILED).exclude(
        check_status__in=(CHECK_REMOVED, CHECK_REJECTED),
    ).exclude(poster_status=ONLINE_REJECTED).filter(
        Exists(PosterConnection.objects.filter(
            partner_id=OuterRef("partner_id"), status=PosterConnection.Status.ACTIVE,
        )),
    )


def with_register_mark(queryset):
    """Orders annotated for ``register_holds`` — a whole list in one query
    (Admin's list, the staff's open orders)."""

    return queryset.annotate(**{HELD: Exists(_held_pushes().filter(
        order_id=OuterRef("pk"), partner_id=OuterRef("partner_id"),
    ))})
