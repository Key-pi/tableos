"""Variants and add-ons of an item (catalog step 2, owner 2026-11-07).

- **Variants** — one list per item with their own price (a size, colour or
  pack: «250 мл», «M», «500 г»). While an item has variants on sale the
  guest picks one; packs stand in for sale by weight for now.
- **Add-ons** — groups set once and attached to items or whole categories
  (a category's group also covers its subcategories): «Молоко» one and
  required, «Сироп» up to two, «Без…» free. A group with nothing on sale is
  not offered and holds nothing back.
- Module ``module_options_enabled``: while it is off nothing is chosen and
  every item is sold at its own price, as before.

One function decides what was chosen and what it costs — ``resolve_choice``
— for the bot, the Mini App, the cart, checkout and the staff quick sale.
The result is a ``Choice``: the line's name (the item, its variant and the
add-ons, so bills, the kitchen screen and refunds show it as it is), its
price, a key that tells two identical choices apart from others, and a
snapshot of the add-ons for the order line.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from core.i18n import _

NAME_LIMIT = 255


class ChoiceError(Exception):
    """The choice cannot be ordered as it is (the message is for the guest)."""


@dataclass
class GroupChoices:
    group: object
    modifiers: list = field(default_factory=list)

    @property
    def required(self) -> bool:
        return self.group.min_choices > 0


@dataclass
class ItemChoices:
    """What can be chosen for an item now: variants and add-on groups on sale."""

    variants: list = field(default_factory=list)
    groups: list = field(default_factory=list)

    def modifiers(self) -> list:
        """All add-ons in the order the guest sees them."""

        return [modifier for entry in self.groups for modifier in entry.modifiers]


NOTHING = ItemChoices()


def options_on(partner_id) -> bool:
    from apps.partners.models import PartnerBotSettings

    return PartnerBotSettings.objects.filter(
        partner_id=partner_id, module_options_enabled=True
    ).exists()


def catalog_choices(partner, items, *, on: bool | None = None) -> dict:
    """``{item.id: ItemChoices}`` for a whole menu in a few queries: the
    module, variants, add-ons with their groups, the groups' items and
    categories. Items need ``category`` loaded (its ``parent_id`` is read)."""

    from apps.menu.models import MenuItemVariant, Modifier, ModifierGroup

    items = list(items)
    partner_id = getattr(partner, "id", partner)
    if on is None:
        on = options_on(partner_id)
    if not on or not items:
        return {item.id: NOTHING for item in items}

    found = {item.id: ItemChoices() for item in items}
    for variant in MenuItemVariant.objects.filter(
        item_id__in=list(found), is_available=True
    ).order_by("sort_order", "price", "name"):
        found[variant.item_id].variants.append(variant)

    # Only groups with something on sale: the add-ons bring their groups.
    groups: dict = {}
    for modifier in (
        Modifier.objects.filter(
            group__partner_id=partner_id, group__is_active=True, is_available=True
        )
        .select_related("group")
        .order_by("group__sort_order", "group__name", "group_id", "sort_order", "name")
    ):
        groups.setdefault(modifier.group_id, GroupChoices(modifier.group)).modifiers.append(
            modifier
        )
    if not groups:
        return found

    by_item: dict = {}
    for group_id, item_id in ModifierGroup.items.through.objects.filter(
        modifiergroup_id__in=list(groups), menuitem_id__in=list(found)
    ).values_list("modifiergroup_id", "menuitem_id"):
        by_item.setdefault(item_id, set()).add(group_id)
    by_category: dict = {}
    for group_id, category_id in ModifierGroup.categories.through.objects.filter(
        modifiergroup_id__in=list(groups)
    ).values_list("modifiergroup_id", "menucategory_id"):
        by_category.setdefault(category_id, set()).add(group_id)

    for item in items:
        attached = set(by_item.get(item.id, ()))
        attached |= by_category.get(item.category_id, set())
        parent_id = item.category.parent_id
        if parent_id:
            attached |= by_category.get(parent_id, set())
        found[item.id].groups = [
            entry for group_id, entry in groups.items() if group_id in attached
        ]
    return found


def choices_for(item, *, on: bool | None = None) -> ItemChoices:
    return catalog_choices(item.partner_id, [item], on=on)[item.id]


def needs_choice(item, choices: ItemChoices | None = None) -> bool:
    """A variant or a required add-on must be picked before it goes to a cart."""

    choices = choices if choices is not None else choices_for(item)
    return bool(choices.variants) or any(entry.required for entry in choices.groups)


def has_choices(item, choices: ItemChoices | None = None) -> bool:
    choices = choices if choices is not None else choices_for(item)
    return bool(choices.variants or choices.groups)


def price_from(item, choices: ItemChoices | None = None) -> Decimal:
    """The lowest price to show on a card: «від 60 грн» while there are variants."""

    choices = choices if choices is not None else choices_for(item)
    if choices.variants:
        return min(variant.price for variant in choices.variants)
    return item.price


def line_name(item_name: str, variant_name: str = "", modifier_names=()) -> str:
    """«Лате, 400 мл (Вівсяне, Карамель)»."""

    name = f"{item_name}, {variant_name}" if variant_name else item_name
    if modifier_names:
        name = f"{name} ({', '.join(modifier_names)})"
    if len(name) > NAME_LIMIT:
        name = name[: NAME_LIMIT - 1] + "…"
    return name


@dataclass(frozen=True)
class Choice:
    """An item with what was chosen for it."""

    item: object
    variant: object = None
    modifiers: tuple = ()

    @property
    def unit_price(self) -> Decimal:
        base = Decimal(self.variant.price if self.variant is not None else self.item.price)
        extra = sum((Decimal(modifier.price) for modifier in self.modifiers), Decimal("0"))
        return (base + extra).quantize(Decimal("0.01"))

    @property
    def name(self) -> str:
        return line_name(
            self.item.name,
            self.variant.name if self.variant is not None else "",
            [modifier.name for modifier in self.modifiers],
        )

    @property
    def key(self) -> str:
        """"" for an item without a choice; else the same for the same choice."""

        if self.variant is None and not self.modifiers:
            return ""
        source = (self.variant.id.hex if self.variant is not None else "-") + ":" + ".".join(
            sorted(modifier.id.hex for modifier in self.modifiers)
        )
        return hashlib.sha256(source.encode()).hexdigest()[:32]

    @property
    def variant_id(self):
        return self.variant.id if self.variant is not None else None

    def snapshot(self) -> list[dict]:
        """The add-ons as they were at the order: kept on the line."""

        return [
            {
                "id": modifier.id.hex,
                "group": modifier.group.name,
                "name": modifier.name,
                "price": f"{Decimal(modifier.price):.2f}",
            }
            for modifier in self.modifiers
        ]


def _hex(value) -> str:
    return uuid.UUID(str(value)).hex


def resolve_choice(
    item, variant_id=None, modifier_ids=(), *, choices: ItemChoices | None = None
) -> Choice:
    """Check what was chosen for ``item`` against what is on sale now and
    return the ``Choice``; ``ChoiceError`` says what is wrong."""

    modifier_ids = list(modifier_ids or ())
    if choices is None:
        choices = choices_for(item)
    if choices is NOTHING or not (choices.variants or choices.groups):
        if variant_id or modifier_ids:
            if choices is NOTHING:  # the module is off
                raise ChoiceError(_("Варианты и добавки сейчас не выбираются."))
            raise ChoiceError(
                _("Этого варианта «{name}» сейчас нет — выберите заново.", name=item.name)
            )
        return Choice(item)

    variant = None
    if choices.variants:
        if not variant_id:
            raise ChoiceError(_("Выберите вариант: «{name}».", name=item.name))
        try:
            wanted = _hex(variant_id)
        except ValueError:
            wanted = ""
        variant = next((row for row in choices.variants if row.id.hex == wanted), None)
        if variant is None:
            raise ChoiceError(
                _("Этого варианта «{name}» сейчас нет — выберите заново.", name=item.name)
            )
    elif variant_id:
        raise ChoiceError(
            _("Этого варианта «{name}» сейчас нет — выберите заново.", name=item.name)
        )

    on_sale = {modifier.id.hex: modifier for modifier in choices.modifiers()}
    chosen: dict = {}
    for raw in modifier_ids:
        try:
            key = _hex(raw)
        except ValueError:
            key = ""
        if key not in on_sale:
            raise ChoiceError(
                _("Этой добавки к «{name}» сейчас нет — выберите заново.", name=item.name)
            )
        chosen[key] = on_sale[key]

    for entry in choices.groups:
        count = sum(1 for modifier in entry.modifiers if modifier.id.hex in chosen)
        group = entry.group
        if count < group.min_choices:
            if group.min_choices == 1:
                raise ChoiceError(
                    _("Выберите «{group}» для «{name}».", group=group.name, name=item.name)
                )
            raise ChoiceError(
                _("«{group}»: выберите не меньше {count}.", group=group.name,
                  count=group.min_choices)
            )
        if group.max_choices and count > group.max_choices:
            raise ChoiceError(
                _("«{group}»: не больше {count}.", group=group.name, count=group.max_choices)
            )

    ordered = tuple(modifier for modifier in choices.modifiers() if modifier.id.hex in chosen)
    return Choice(item, variant, ordered)
