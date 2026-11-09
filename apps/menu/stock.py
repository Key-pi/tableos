"""Stock of items and variants (catalog step 3, owner 2026-11-07).

Module ``module_stock_enabled``; an item is counted only with
``track_stock`` («Учитывать остаток»). An item with variants counts each
variant (``MenuItemVariant.stock_quantity``), a line without a variant counts
the item (``MenuItem.stock_quantity``).

Owner's decisions:

- an order takes its goods **at checkout** (``reserve_for_order`` from
  ``create_order``): two guests cannot buy the last one; a canceled order
  gives them back (``release_order`` from ``transition_order_status``);
- the guest sees «нет в наличии» (not orderable) and «осталось мало» (up to
  ``LOW_STOCK``), never the number (``state_of``);
- the staff quick sale takes too and may go below zero
  (``take_for_walk_in_sale``): the goods are in the buyer's hands, the count
  is what is wrong;
- a refund puts goods back only when the staff tick it
  (``return_for_refund``).

Every change is a ``StockMovement`` with what is left after it. What an
order still holds is what its movements took minus what they gave back, so a
cancel or a refund never gives back more than the order took.

Locks: the stock rows last in a transaction, items then variants, each in id
order, ``FOR NO KEY UPDATE`` — new order lines referencing the item still
pass their foreign-key check.
"""

from __future__ import annotations

from core.i18n import _

LOW_STOCK = 5
ORDER_SOURCE = "order"


class StockError(Exception):
    """Not enough in stock (the message is for the guest or the staff)."""


def stock_on(partner_id) -> bool:
    from apps.partners.models import PartnerBotSettings

    return PartnerBotSettings.objects.filter(
        partner_id=partner_id, module_stock_enabled=True
    ).exists()


def counted(item, *, on: bool | None = None) -> bool:
    if not item.track_stock:
        return False
    return stock_on(item.partner_id) if on is None else on


def level(item, variant=None, *, on: bool | None = None) -> int | None:
    """What is in stock, or None when it is not counted."""

    if not counted(item, on=on):
        return None
    return variant.stock_quantity if variant is not None else item.stock_quantity


def state_of(value: int | None) -> str:
    """What the guest is told: "out", "low" or nothing."""

    if value is None:
        return ""
    if value <= 0:
        return "out"
    return "low" if value <= LOW_STOCK else ""


def item_state(item, variants=(), *, on: bool | None = None) -> str:
    """For a card: out when nothing is left (of any variant on sale), low
    when every one has little."""

    if not counted(item, on=on):
        return ""
    if variants:
        return state_of(max(variant.stock_quantity for variant in variants))
    return state_of(item.stock_quantity)


def _name(item, variant) -> str:
    """In the reader's language (catalog step 4)."""

    from apps.menu.translations import translated

    name = translated(item, "name")
    return f"{name}, {translated(variant, 'name')}" if variant is not None else name


def check_room(item, variant, wanted: int) -> None:
    """``wanted`` — how many the cart would hold after the change."""

    value = level(item, variant)
    if value is None:
        return
    if value <= 0:
        raise StockError(_("«{name}» нет в наличии.", name=_name(item, variant)))
    if wanted > value:
        raise StockError(_("Больше «{name}» нет в наличии.", name=_name(item, variant)))


def _lock(keys):
    """The stock rows of ``(item id, variant id | None)``, locked."""

    from apps.menu.models import MenuItem, MenuItemVariant

    item_ids = sorted({item_id for item_id, variant_id in keys if variant_id is None})
    variant_ids = sorted({variant_id for _item_id, variant_id in keys if variant_id})
    rows = {}
    for item in (
        MenuItem.objects.select_for_update(no_key=True).filter(id__in=item_ids).order_by("id")
    ):
        rows[(item.id, None)] = item
    for variant in (
        MenuItemVariant.objects.select_for_update(no_key=True)
        .filter(id__in=variant_ids)
        .order_by("id")
    ):
        rows[(variant.item_id, variant.id)] = variant
    return rows


def _record(row, key, delta: int, reason: str, source: tuple[str, str], actor=None):
    from apps.menu.models import StockMovement

    row.stock_quantity += delta
    row.save(update_fields=["stock_quantity", "updated_at"])
    StockMovement.objects.create(
        partner_id=row.partner_id,
        item_id=key[0],
        variant_id=key[1],
        delta=delta,
        balance_after=row.stock_quantity,
        reason=reason,
        source_type=source[0],
        source_id=source[1],
        actor=actor,
    )


def _sorted(keys):
    return sorted(keys, key=lambda key: (key[1] is not None, str(key[1] or key[0])))


def _wanted(lines) -> tuple[dict, dict]:
    """{key: quantity} and {key: name} of counted lines (``menu_item`` and
    ``variant`` loaded, ``track_stock`` on)."""

    wanted, names = {}, {}
    for line in lines:
        if not line.menu_item_id or not line.menu_item.track_stock:
            continue
        key = (line.menu_item_id, line.variant_id)
        wanted[key] = wanted.get(key, 0) + line.quantity
        names[key] = _name(line.menu_item, line.variant)
    return wanted, names


def reserve_for_order(order) -> None:
    """Take an order's goods at checkout; ``StockError`` when there is not
    enough of something (nothing is taken then)."""

    from apps.menu.models import StockMovement

    if not stock_on(order.partner_id):
        return
    lines = order.items.filter(kind="item").select_related("menu_item", "variant")
    wanted, names = _wanted(lines)
    if not wanted:
        return
    rows = _lock(wanted)
    for key, quantity in wanted.items():
        row = rows.get(key)
        if row is None:
            continue
        if row.stock_quantity <= 0:
            raise StockError(
                _("«{name}» закончился — уберите его из корзины.", name=names[key])
            )
        if row.stock_quantity < quantity:
            raise StockError(
                _("«{name}» осталось меньше, чем в корзине — уменьшите количество.",
                  name=names[key])
            )
    for key in _sorted(wanted):
        if key in rows:
            _record(rows[key], key, -wanted[key], StockMovement.Reason.ORDER,
                    (ORDER_SOURCE, str(order.id)))


def held_by_order(order_id) -> dict:
    """{key: quantity} the order still holds."""

    from apps.menu.models import StockMovement

    held: dict = {}
    for item_id, variant_id, delta in StockMovement.objects.filter(
        source_type=ORDER_SOURCE, source_id=str(order_id)
    ).values_list("item_id", "variant_id", "delta"):
        held[(item_id, variant_id)] = held.get((item_id, variant_id), 0) - delta
    return {key: quantity for key, quantity in held.items() if quantity > 0}


def release_order(order, actor=None) -> None:
    """Give back what a canceled order still holds (whatever the module is
    now: it gives back what it took)."""

    from apps.menu.models import StockMovement

    held = held_by_order(order.id)
    if not held:
        return
    rows = _lock(held)
    for key in _sorted(held):
        if key in rows:
            _record(rows[key], key, held[key], StockMovement.Reason.CANCEL,
                    (ORDER_SOURCE, str(order.id)), actor)


def _returnable(bill_items) -> dict:
    """{(order id, key): quantity} refunded lines can give back: no more
    than their order still holds."""

    by_order: dict = {}
    for bill_item in bill_items:
        line = bill_item.order_item
        if line is None or not line.menu_item_id:
            continue
        key = (line.menu_item_id, line.variant_id)
        wanted = by_order.setdefault(line.order_id, {})
        wanted[key] = wanted.get(key, 0) + line.quantity
    back: dict = {}
    for order_id, wanted in by_order.items():
        held = held_by_order(order_id)
        for key, quantity in wanted.items():
            amount = min(quantity, held.get(key, 0))
            if amount > 0:
                back[(order_id, key)] = amount
    return back


def returnable(bill_items) -> bool:
    """Whether «вернуть на склад» would put anything back (the tick is
    offered only then)."""

    return bool(_returnable(bill_items))


def return_for_refund(bill_items, actor=None) -> None:
    """Put refunded lines back in stock (the staff ticked it)."""

    from apps.menu.models import StockMovement

    back = _returnable(bill_items)
    if not back:
        return
    rows = _lock({key for _order_id, key in back})
    for (order_id, key), amount in sorted(back.items(), key=lambda pair: str(pair[0])):
        if key in rows:
            _record(rows[key], key, amount, StockMovement.Reason.REFUND,
                    (ORDER_SOURCE, str(order_id)), actor)


def take_for_walk_in_sale(sale, actor=None) -> None:
    """The quick sale takes its goods; the stock may go below zero."""

    from apps.menu.models import StockMovement

    if not stock_on(sale.partner_id):
        return
    wanted, _names = _wanted(sale.items.select_related("menu_item", "variant"))
    if not wanted:
        return
    rows = _lock(wanted)
    for key in _sorted(wanted):
        if key in rows:
            _record(rows[key], key, -wanted[key], StockMovement.Reason.QUICK_SALE,
                    ("walk_in_sale", str(sale.id)), actor)


def count_stock(*, item, variant, quantity: int, actor=None) -> None:
    """The owner counted what there is (Admin): the level becomes ``quantity``."""

    from apps.menu.models import StockMovement

    if not item.track_stock:
        raise StockError(_("Сначала включите у позиции «Учитывать остаток»."))
    key = (item.id, variant.id if variant is not None else None)
    row = _lock({key}).get(key)
    if row is None:
        raise StockError(_("Позиция не найдена."))
    delta = int(quantity) - row.stock_quantity
    if delta:
        _record(row, key, delta, StockMovement.Reason.COUNT, ("admin", ""), actor)


# --- for Admin: which counted items need attention (admin step 2, PQ-050) ----

OUT = "out"
LOW = "low"
BELOW_ZERO = "below_zero"


def with_stock_bounds(queryset, *, variants: bool = True):
    """Items with the most and the least left: over their variants on sale,
    else the item's own count (as the guest's card counts it). Without the
    options module there are no variants to choose (``variants=False``)."""

    from django.db.models import F, Max, Min, Q
    from django.db.models.functions import Coalesce

    if not variants:
        return queryset.annotate(stock_most=F("stock_quantity"), stock_least=F("stock_quantity"))
    shown = Q(variants__is_available=True)
    return queryset.annotate(
        stock_most=Coalesce(Max("variants__stock_quantity", filter=shown), "stock_quantity"),
        stock_least=Coalesce(Min("variants__stock_quantity", filter=shown), "stock_quantity"),
    )


def items_in_stock_state(queryset, state: str, *, variants: bool = True):
    """Counted items that are out (nothing of any variant on sale), low (up
    to ``LOW_STOCK``) or below zero (a quick sale took more than was counted)."""

    counted = with_stock_bounds(queryset.filter(track_stock=True), variants=variants)
    if state == OUT:
        return counted.filter(stock_most__lte=0)
    if state == LOW:
        return counted.filter(stock_most__gt=0, stock_most__lte=LOW_STOCK)
    if state == BELOW_ZERO:
        return counted.filter(stock_least__lt=0)
    return queryset.none()
