"""The catalog beyond names and prices (owner, 2026-11-07).

- **Schedule** of a category: shown all day, sold only in its time — from
  ``available_from`` until ``available_until`` (a window past midnight such as
  22:00–02:00 belongs to the day it starts), on ``available_weekdays`` (empty:
  every day). A subcategory is sold only while its parent is too. Checked
  when an item goes into a cart and again at checkout
  (``not_on_sale_message``); staff quick sales are not limited.
- **Showcase** (module ``module_showcase_enabled``): portion, old price,
  labels, more photos, characteristics, SKU — shown to guests only while the
  module is on (``showcase_on``).
- **Ways of ordering**: an item is sold where its marks say
  (``MenuItem.is_sold_in``); a venue on Poster with «Только меню Poster»
  does not sell Botera's own items in pickup and delivery — they go to the
  register, which would not take them (PQ-058, step 7). ``not_sold_in`` and
  ``sold_in_channels`` answer for the cart, checkout and the Mini App;
  ``apps/menu/repositories.py`` filters the menu the same way.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from apps.integrations.poster.selectors import REGISTER_CHANNELS, linked_ids, own_menu_hidden
from apps.menu.translations import translated
from core.i18n import EN, N_, _, active_language

# Ready labels, translated; the owner adds own ones (MenuBadge).
BUILTIN_BADGES = {
    "new": ("🆕", N_("Новинка")),
    "hit": ("🔥", N_("Хит")),
    "recommended": ("👍", N_("Рекомендуем")),
    "spicy": ("🌶", N_("Острое")),
    "vegan": ("🌱", N_("Веган")),
    "vegetarian": ("🥦", N_("Вегетарианское")),
    "gluten_free": ("🌾", N_("Без глютена")),
    "lactose_free": ("🥛", N_("Без лактозы")),
    "adults": ("🔞", N_("18+")),
    "limited": ("⏳", N_("Ограниченное предложение")),
    "eco": ("♻️", N_("Эко")),
    "handmade": ("✋", N_("Ручная работа")),
}

WEEKDAYS = (N_("пн"), N_("вт"), N_("ср"), N_("чт"), N_("пт"), N_("сб"), N_("вс"))


# --- schedule ------------------------------------------------------------------


def local_now(partner) -> datetime:
    zone = ZoneInfo(getattr(partner, "timezone", "") or settings.TIME_ZONE)
    return timezone.now().astimezone(zone)


def _day_ok(category, weekday: int) -> bool:
    days = category.available_weekdays or []
    return not days or weekday in days


def _open_by_itself(category, now: datetime) -> bool:
    start, end = category.available_from, category.available_until
    weekday, moment = now.weekday(), now.time()
    if start is None or end is None:
        return _day_ok(category, weekday)
    if start < end:
        return _day_ok(category, weekday) and start <= moment < end
    # Past midnight: the evening part on its own day, the night on the next.
    if moment >= start:
        return _day_ok(category, weekday)
    if moment < end:
        return _day_ok(category, (weekday - 1) % 7)
    return False


def category_open(category, now: datetime | None = None) -> bool:
    now = now or local_now(category.partner)
    if not _open_by_itself(category, now):
        return False
    return category.parent_id is None or _open_by_itself(category.parent, now)


def schedule_label(category) -> str:
    """When the category is sold, e.g. «с 08:00 до 12:00, пн, вт, ср»; ""
    for all the time."""

    parts = []
    if category.available_from is not None and category.available_until is not None:
        parts.append(
            _(
                "с {start} до {end}",
                start=category.available_from.strftime("%H:%M"),
                end=category.available_until.strftime("%H:%M"),
            )
        )
    days = sorted(category.available_weekdays or [])
    if days and len(days) < 7:
        parts.append(", ".join(_(WEEKDAYS[day]) for day in days))
    return ", ".join(parts)


def closed_category(category, now: datetime | None = None):
    """The category (itself or its parent) not on sale now, or None."""

    now = now or local_now(category.partner)
    if not _open_by_itself(category, now):
        return category
    if category.parent_id and not _open_by_itself(category.parent, now):
        return category.parent
    return None


def not_on_sale_message(item, now: datetime | None = None) -> str | None:
    """Why the item cannot be ordered now (its category's time), or None."""

    closed = closed_category(item.category, now)
    if closed is None:
        return None
    return _(
        "«{name}» продаётся {when} — сейчас заказать нельзя.",
        name=translated(item, "name"), when=schedule_label(closed),
    )


# --- ways of ordering -------------------------------------------------------------


def not_sold_in(menu_items, channel) -> set:
    """Ids of the items not sold in ``channel`` now: their marks, then
    Botera's own items where the register takes the orders (step 7)."""

    items = list(menu_items)
    off = {item.id for item in items if not item.is_sold_in(channel)}
    rest = [item for item in items if item.id not in off]
    if rest and own_menu_hidden(rest[0].partner_id, channel):
        from apps.menu.models import MenuItem

        linked = linked_ids(rest[0].partner_id, MenuItem, [item.id for item in rest])
        off |= {item.id for item in rest if item.id not in linked}
    return off


def sold_in_channels(menu_items) -> dict:
    """``{item.id: [channel, ...]}`` — where each item is sold now, for the
    Mini App, which shows the menu of every way at once."""

    items = list(menu_items)
    if not items:
        return {}
    from apps.menu.models import MenuItem

    hidden = {
        channel for channel in REGISTER_CHANNELS if own_menu_hidden(items[0].partner_id, channel)
    }
    linked = (
        linked_ids(items[0].partner_id, MenuItem, [item.id for item in items]) if hidden else set()
    )
    return {
        item.id: [
            channel for channel in MenuItem.CHANNEL_FIELDS
            if item.is_sold_in(channel) and (channel not in hidden or item.id in linked)
        ]
        for item in items
    }


# --- showcase ------------------------------------------------------------------


def showcase_on(partner) -> bool:
    from apps.partners.models import PartnerBotSettings

    try:
        settings_row = partner.bot_settings
    except PartnerBotSettings.DoesNotExist:
        return False
    return bool(settings_row.module_showcase_enabled)


def number_label(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if active_language() == EN else text.replace(".", ",")


def portion_label(item) -> str:
    if item.portion is None or not item.portion_unit:
        return ""
    return f"{number_label(item.portion)} {item.get_portion_unit_display()}"


def badges_of(item) -> list[dict]:
    """Ready labels in the reader's language, then the owner's own."""

    found = [
        {"emoji": BUILTIN_BADGES[code][0], "name": _(BUILTIN_BADGES[code][1])}
        for code in item.badges or []
        if code in BUILTIN_BADGES
    ]
    found += [
        {"emoji": badge.emoji, "name": translated(badge, "name")}
        for badge in item.custom_badges.all()
    ]
    return found


def has_old_price(item) -> bool:
    return item.old_price is not None and item.old_price > item.price


def first_not_on_sale(menu_items) -> str | None:
    """The message for the first item not on sale now (its category's time),
    or None; the time is the venue's own."""

    now = None
    for item in menu_items:
        if now is None:
            now = local_now(item.partner)
        message = not_on_sale_message(item, now)
        if message:
            return message
    return None


# --- the tree of categories -----------------------------------------------------


def catalog_tree(partner_id, items) -> list[dict]:
    """Top categories in order, each with its own items and subcategories
    that have something to show: ``[{"category", "items", "children":
    [{"category", "items"}]}]``. ``items`` — the items to show (active, sold
    in the chosen way); a top category is kept when only its subcategories
    have items."""

    from apps.menu.models import MenuCategory

    by_category: dict = {}
    for item in items:
        by_category.setdefault(item.category_id, []).append(item)
    categories = list(
        MenuCategory.objects.filter(partner_id=partner_id, is_active=True)
        .select_related("partner", "parent")
        .order_by("sort_order", "name")
    )
    tree = []
    for top in (category for category in categories if category.parent_id is None):
        children = [
            {"category": child, "items": by_category[child.id]}
            for child in categories
            if child.parent_id == top.id and by_category.get(child.id)
        ]
        own = by_category.get(top.id, [])
        if own or children:
            tree.append({"category": top, "items": own, "children": children})
    return tree
