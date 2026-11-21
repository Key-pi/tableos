"""The menu from Poster (PQ-058, step 3; plan — docs/architecture/POSTER.md,
«Меню из Poster»).

``sync_menu`` reads Poster first, outside any transaction: the whole menu
with variants and sets of modifications comes in two requests
(``menu.getCategories``, ``menu.getProducts``). Then it writes in one
transaction under the lock of the venue's ``PosterConnection`` row, so two
syncs of one venue (the button, the night) follow each other and the second
finds the references of the first.

What comes over, in Poster's units:

- categories → ``MenuCategory``, two levels (Botera's limit): a deeper Poster
  category gives its items to its second-level ancestor, and hides them if
  it is hidden itself. A Botera category of the same name that is not linked
  yet is taken over (names are unique in a venue); two Poster categories of
  one name get the parent's name in brackets;
- products (type 3) and tech cards (type 2) → ``MenuItem``; semi-finished
  products (type 1) are not sold. Prices of products and variants are in
  kopecks, the price of this spot; an item with variants costs as its
  cheapest one, the guest picks the variant. Without a price for the spot
  the product is not brought (``SyncReport.skipped``);
- variants (``modifications``) → ``MenuItemVariant``;
- sets of modifications of tech cards → ``ModifierGroup`` on every item that
  has the set; add-on prices are in hryvnias; a set «one of» (type 1) is
  required — 1 and 1 (Poster refuses an order without it, code 210);
- the photo → ``image_url`` (``https://joinposter.com`` + path; opens without
  a token); a photo uploaded in Botera wins anyway (``MenuItem.photo_url``).

Poster owns names, prices, categories, variants, add-ons and what is on sale
(``selectors.POSTER_FIELDS``, read only in Admin); translations, descriptions,
marks, own photos, ways of ordering and the order of rows are Botera's and
stay. Removed in Poster is hidden, never deleted. A category or an item is
shown or hidden only when Poster changes its mind (``PosterRef.hidden_in_poster``):
the stop list of Botera survives the night.

Locks: ``PosterConnection`` → the venue's linked ``MenuItem`` rows by id →
their ``MenuItemVariant`` rows by id (``no_key``, as orders take them); then
the rest is written.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.integrations.poster.client import PosterClient, PosterError
from apps.integrations.poster.models import PosterConnection, PosterRef
from apps.integrations.poster.services import may_manage_poster
from apps.menu.models import MenuCategory, MenuItem, MenuItemVariant, Modifier, ModifierGroup
from apps.menu.options import options_on
from apps.partners.languages import main_language
from apps.partners.models import PartnerBotSettings
from core.i18n import _, gettext_in

logger = logging.getLogger(__name__)

Kind = PosterRef.Kind
PHOTO_HOST = "https://joinposter.com"
# A big menu is one answer of a few megabytes.
MENU_TIMEOUT = 30.0
CENT = Decimal("0.01")
SOLD_TYPES = {"2", "3"}  # tech card, product; 1 is a semi-finished product
ONE_OF = "1"
NO_CATEGORY = "0"
DEPTH = 2  # Botera's categories: a top one and one inside it
NAME_SHOWN = 10


class PosterSyncError(Exception):
    """Why the menu was not brought, in words for the venue's owner."""


@dataclass
class SyncReport:
    """What one sync did, by kind of object (``PosterRef.Kind``)."""

    created: Counter = field(default_factory=Counter)
    updated: Counter = field(default_factory=Counter)
    removed: Counter = field(default_factory=Counter)
    skipped: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def summary(self) -> str:
        text = _(
            "Меню обновлено из Poster. Позиции: новых — {created}, изменено — {updated}, "
            "скрыто — {removed}.",
            created=self.created[Kind.PRODUCT], updated=self.updated[Kind.PRODUCT],
            removed=self.removed[Kind.PRODUCT],
        )
        others = sum(
            counter[kind] for counter in (self.created, self.updated, self.removed)
            for kind in counter if kind != Kind.PRODUCT
        )
        if others:
            text += " " + _("Категорий, вариантов и добавок изменено: {count}.", count=others)
        return text

    def notes(self) -> list[str]:
        """Warnings for the owner, after the summary."""

        notes = list(self.warnings)
        if self.skipped:
            shown = "; ".join(self.skipped[:NAME_SHOWN])
            if len(self.skipped) > NAME_SHOWN:
                shown += " " + _("и ещё {count}", count=len(self.skipped) - NAME_SHOWN)
            notes.append(_("Не перенесено из Poster: {names}.", names=shown))
        return notes


# --- reading Poster ----------------------------------------------------------------


def _rows(answer) -> list[dict]:
    if not isinstance(answer, list):
        return []
    return [row for row in answer if isinstance(row, dict)]


def fetch_menu(token: str) -> tuple[list[dict], list[dict]]:
    """Categories and products of the account; network only."""

    with PosterClient(token, timeout=MENU_TIMEOUT) as client:
        categories = client.call("menu.getCategories")
        products = client.call("menu.getProducts")
    return _rows(categories), _rows(products)


def _require_working(connection: PosterConnection) -> None:
    if (
        connection.status != PosterConnection.Status.ACTIVE
        or not connection.spot_id
        or not connection.access_token_encrypted
    ):
        raise PosterSyncError(_("Poster не подключён — меню из него не обновить."))


def _remember_error(connection_id, message: str) -> None:
    with transaction.atomic():
        connection = PosterConnection.objects.select_for_update().get(pk=connection_id)
        connection.last_error = message
        connection.last_error_at = timezone.now()
        connection.save(update_fields=["last_error", "last_error_at", "updated_at"])


def sync_menu(*, connection_id, user=None) -> SyncReport:
    """Bring the venue's menu from Poster. ``user`` — who pressed the button
    (checked again); the night and the webhooks run without one."""

    connection = PosterConnection.objects.filter(pk=connection_id).first()
    if connection is None:
        raise PosterSyncError(_("Подключение к Poster не найдено."))
    if user is not None and not may_manage_poster(user, connection.partner_id):
        raise PosterSyncError(_("Обновлять меню из Poster может владелец заведения."))
    _require_working(connection)
    account_number = connection.account_number

    # Network first, outside any transaction.
    try:
        categories, products = fetch_menu(connection.get_access_token())
    except PosterError as exc:
        message = _("Poster не отдал меню: {reason}", reason=exc.message)
        _remember_error(connection.pk, message)
        raise PosterSyncError(message) from None

    with transaction.atomic():
        connection = PosterConnection.objects.select_for_update().get(pk=connection.pk)
        _require_working(connection)
        if connection.account_number != account_number:
            raise PosterSyncError(_(
                "Подключение к Poster изменилось, пока читалось меню. Обновите меню ещё раз."
            ))
        report = _MenuWriter(connection).write(categories, products)
        connection.last_synced_at = timezone.now()
        connection.last_error = ""
        connection.last_error_at = None
        connection.save(update_fields=[
            "last_synced_at", "last_error", "last_error_at", "updated_at",
        ])
    logger.debug(
        "Poster menu synced for partner %s: %s created, %s updated, %s removed",
        connection.partner_id, *(
            {str(kind): count for kind, count in counter.items()}
            for counter in (report.created, report.updated, report.removed)
        ),
    )
    return report


# --- Poster's values -----------------------------------------------------------------


def _clip(value, model, name: str) -> str:
    return str(value or "").strip()[: model._meta.get_field(name).max_length]


def _int(value, default: int = 0) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return min(max(number, 0), 32767)


def _kopecks(value) -> Decimal | None:
    """A positive price in kopecks → hryvnias; anything else → None."""

    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return (Decimal(number) / 100).quantize(CENT)


def _hryvnias(value) -> Decimal | None:
    """An add-on's price, already in hryvnias; 0 is free."""

    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount.quantize(CENT)


def _spot_entry(entries, spot: str) -> dict | None:
    for entry in entries or []:
        if isinstance(entry, dict) and str(entry.get("spot_id")) == spot:
            return entry
    return None


def _shown(entry) -> bool:
    return entry is None or str(entry.get("visible", "1")) != "0"


def _photo(row) -> str:
    path = str(row.get("photo") or "").strip()
    if path.startswith("/"):
        url = PHOTO_HOST + path
    elif path.startswith("https://"):
        url = path
    else:
        return ""
    return url if len(url) <= MenuItem._meta.get_field("image_url").max_length else ""


@dataclass
class _Variant:
    key: str
    name: str
    price: Decimal | None
    visible: bool
    order: int


def _load_refs(partner_id) -> dict:
    """The venue's references, read under the connection's lock."""

    return {
        (ref.kind, ref.poster_id): ref
        for ref in PosterRef.objects.filter(partner_id=partner_id)
    }


# --- writing Botera's menu -------------------------------------------------------------


class _MenuWriter:
    def __init__(self, connection: PosterConnection) -> None:
        self.partner_id = connection.partner_id
        self.spot = str(connection.spot_id)
        self.report = SyncReport()
        self.refs = _load_refs(self.partner_id)
        self.seen: set = set()
        linked = {kind: [] for kind in Kind.values}
        for (kind, _poster_id), ref in self.refs.items():
            linked[kind].append(ref.object_id)
        partner = {"partner_id": self.partner_id}
        # Items, then variants, each by id — as orders lock them (locks.md).
        self.items = {
            item.pk: item for item in MenuItem.objects.select_for_update(no_key=True)
            .filter(pk__in=linked[Kind.PRODUCT], **partner).order_by("pk")
        }
        self.variants = {
            variant.pk: variant for variant in MenuItemVariant.objects.select_for_update(
                no_key=True,
            ).filter(pk__in=linked[Kind.VARIANT], **partner).order_by("pk")
        }
        self.categories = {
            category.pk: category for category in MenuCategory.objects.filter(**partner)
        }
        self.names = {category.name: category.pk for category in self.categories.values()}
        self.linked_categories = set(linked[Kind.CATEGORY])
        self.groups = {
            group.pk: group
            for group in ModifierGroup.objects.filter(pk__in=linked[Kind.MODIFIER_GROUP],
                                                      **partner)
        }
        self.modifiers = {
            modifier.pk: modifier
            for modifier in Modifier.objects.filter(pk__in=linked[Kind.MODIFIER], **partner)
        }
        self.target: dict = {}  # Poster category id → Botera category for its items
        self.hidden_branch: dict = {}  # a hidden folded category on the way
        self.moving: set = set()  # second-level categories being placed now
        self.has_options = False

    def write(self, categories: list[dict], products: list[dict]) -> SyncReport:
        self._categories(categories)
        self._products(products)
        self._sweep()
        if self.has_options and not options_on(self.partner_id):
            self.report.warnings.append(_(
                "У позиций из Poster есть варианты или добавки, а модуль «Варианты и "
                "добавки» выключен — гость их не увидит. Включите его: «Настройки бота» → "
                "«Каталог»."
            ))
        return self.report

    # --- references --------------------------------------------------------------

    def _linked(self, kind, poster_id, objects: dict):
        """The reference and its Botera object (None when there is none)."""

        key = (kind, str(poster_id))
        self.seen.add(key)
        ref = self.refs.get(key)
        return key, ref, (objects.get(ref.object_id) if ref is not None else None)

    def _link(self, key, ref, obj, *, hidden: bool = False) -> None:
        if ref is None:
            self.refs[key] = PosterRef.objects.create(
                partner_id=self.partner_id, kind=key[0], poster_id=key[1], object_id=obj.pk,
                hidden_in_poster=hidden,
            )
            return
        changed = []
        for name, value in (("object_id", obj.pk), ("removed_in_poster", False),
                            ("hidden_in_poster", hidden)):
            if getattr(ref, name) != value:
                setattr(ref, name, value)
                changed.append(name)
        if changed:
            ref.save(update_fields=[*changed, "updated_at"])

    @staticmethod
    def _fresh(ref, obj) -> bool:
        """A first link, a new object or a return from removal: Poster's
        «shown» applies as it is."""

        return ref is None or ref.removed_in_poster or ref.object_id != obj.pk

    def _shown_now(self, ref, obj, visible: bool, current: bool) -> bool:
        """Poster's visibility, but only when Poster changed it; otherwise
        what Botera has (its stop list)."""

        if self._fresh(ref, obj) or ref.hidden_in_poster == visible:
            return visible
        return current

    def _save(self, obj, kind, changed: list[str]) -> None:
        if changed:
            obj.save(update_fields=[*changed, "updated_at"])
            self.report.updated[kind] += 1

    @staticmethod
    def _assign(obj, values: dict) -> list[str]:
        changed = []
        for name, value in values.items():
            current = getattr(obj, f"{name}_id") if name in ("category", "parent", "group") \
                else getattr(obj, name)
            wanted = getattr(value, "pk", value) if name in ("category", "parent", "group") \
                else value
            if current != wanted:
                setattr(obj, name, value)
                changed.append(name)
        return changed

    # --- categories --------------------------------------------------------------

    @staticmethod
    def _category_visible(row) -> bool:
        return str(row.get("category_hidden", "0")) != "1"

    def _category_shown(self, row) -> bool:
        return self._category_visible(row) and _shown(_spot_entry(row.get("visible"), self.spot))

    @staticmethod
    def _chain(category_id: str, by_id: dict) -> list[str]:
        """Poster ids from the top category down to this one."""

        chain = [category_id]
        parent = str(by_id[category_id].get("parent_category") or NO_CATEGORY)
        while parent != NO_CATEGORY and parent in by_id and parent not in chain and len(chain) < 50:
            chain.insert(0, parent)
            parent = str(by_id[parent].get("parent_category") or NO_CATEGORY)
        return chain

    def _free_name(self, name: str, pk, parent_name: str = "") -> str:
        def free(candidate):
            return self.names.get(candidate) in (None, pk)

        if free(name):
            return name
        limit = MenuCategory._meta.get_field("name").max_length
        if parent_name:
            candidate = f"{name} ({parent_name})"[:limit]
            if free(candidate):
                return candidate
        number = 2
        while not free(f"{name} ({number})"[:limit]):
            number += 1
        return f"{name} ({number})"[:limit]

    def _rename(self, category, name: str) -> None:
        if self.names.get(category.name) == category.pk:
            del self.names[category.name]
        category.name = name
        self.names[name] = category.pk

    def _category(self, poster_id: str, name: str, visible: bool, parent_name: str = ""):
        key, ref, obj = self._linked(Kind.CATEGORY, poster_id, self.categories)
        if obj is None:
            # A Botera category of the same name not linked yet is taken over.
            pk = self.names.get(name)
            if pk is not None and pk not in self.linked_categories:
                obj = self.categories[pk]
        if obj is None:
            obj = MenuCategory(partner_id=self.partner_id, is_active=visible)
            self._rename(obj, self._free_name(name, None, parent_name))
            obj.save()
            self.names[obj.name] = obj.pk
            self.categories[obj.pk] = obj
            self.report.created[Kind.CATEGORY] += 1
        else:
            changed = []
            wanted = self._free_name(name, obj.pk, parent_name)
            if obj.name != wanted:
                self._rename(obj, wanted)
                changed.append("name")
            shown = self._shown_now(ref, obj, visible, obj.is_active)
            if obj.is_active != shown:
                obj.is_active = shown
                changed.append("is_active")
            self._save(obj, Kind.CATEGORY, changed)
        self.linked_categories.add(obj.pk)
        self._link(key, ref, obj, hidden=not visible)
        return obj

    def _place(self, category, parent) -> None:
        """Botera's two levels: a category with its own subcategories stays
        on top."""

        parent_id = parent.pk if parent is not None else None
        if category.parent_id == parent_id:
            return
        if parent is not None and any(
            other.parent_id == category.pk and other.pk not in self.moving
            for other in self.categories.values()
        ):
            self.report.warnings.append(_(
                "Категория «{name}» осталась верхней: в ней есть подкатегории Botera.",
                name=category.name,
            ))
            parent = None
            if category.parent_id is None:
                return
        category.parent = parent
        self._save(category, Kind.CATEGORY, ["parent"])

    def _categories(self, rows: list[dict]) -> None:
        by_id = {
            str(row.get("category_id")): row for row in rows
            if str(row.get("category_id") or NO_CATEGORY) != NO_CATEGORY
        }
        chains = {category_id: self._chain(category_id, by_id) for category_id in by_id}
        for depth in range(1, DEPTH + 1):
            for category_id, chain in chains.items():
                if len(chain) != depth:
                    continue
                row = by_id[category_id]
                name = _clip(row.get("category_name"), MenuCategory, "name") or f"#{category_id}"
                parent_name = (
                    str(by_id[chain[0]].get("category_name") or "").strip() if depth > 1 else ""
                )
                self.target[category_id] = self._category(
                    category_id, name, self._category_shown(row), parent_name,
                )
        # Places after all are known, so nothing ends up three deep: what
        # Poster no longer has on these levels leaves its place, then tops,
        # then the second level.
        placed = {self.target[cid].pk for cid, chain in chains.items() if len(chain) <= DEPTH}
        self.moving = {
            self.target[cid].pk for cid, chain in chains.items() if len(chain) == DEPTH
        }
        for (kind, _poster_id), ref in self.refs.items():
            category = self.categories.get(ref.object_id) if kind == Kind.CATEGORY else None
            if category is not None and category.pk not in placed and category.parent_id:
                category.parent = None
                self._save(category, Kind.CATEGORY, ["parent"])
        tops = [cid for cid, chain in chains.items() if len(chain) == 1]
        for category_id in tops:
            self._place(self.target[category_id], None)
        for category_id, chain in chains.items():
            if len(chain) == DEPTH:
                self._place(self.target[category_id], self.target[chain[0]])
            elif len(chain) > DEPTH:
                self.target[category_id] = self.target[chain[DEPTH - 1]]
                self.hidden_branch[category_id] = any(
                    not self._category_shown(by_id[folded]) for folded in chain[DEPTH:]
                )

    def _no_category(self):
        if NO_CATEGORY not in self.target:
            settings = PartnerBotSettings.objects.filter(partner_id=self.partner_id).first()
            name = gettext_in(main_language(settings), "Без категории")
            category = self._category(NO_CATEGORY, name, True)
            self._place(category, None)
            self.target[NO_CATEGORY] = category
        return self.target[NO_CATEGORY]

    # --- items and variants ---------------------------------------------------------

    def _product_shown(self, row) -> bool:
        return str(row.get("hidden", "0")) != "1" and _shown(_spot_entry(row.get("spots"),
                                                                         self.spot))

    def _price(self, row) -> Decimal | None:
        entry = _spot_entry(row.get("spots"), self.spot)
        if entry is not None:
            return _kopecks(entry.get("price"))
        prices = row.get("price")
        if isinstance(prices, dict):
            return _kopecks(prices.get(self.spot))
        return None

    def _variant_rows(self, product_id: str, row) -> list[_Variant]:
        variants = []
        for index, entry in enumerate(row.get("modifications") or []):
            if not isinstance(entry, dict) or not str(entry.get("modificator_id") or ""):
                continue
            spot = _spot_entry(entry.get("spots"), self.spot)
            variants.append(_Variant(
                key=PosterRef.variant_key(product_id, entry["modificator_id"]),
                name=_clip(entry.get("modificator_name"), MenuItemVariant, "name"),
                price=_kopecks(spot.get("price")) if spot is not None else None,
                visible=spot is not None and _shown(spot),
                order=index + 1,
            ))
        return [variant for variant in variants if variant.name]

    def _products(self, rows: list[dict]) -> None:
        groups: dict = {}
        for row in rows:
            product_id = str(row.get("product_id") or "")
            if not product_id or str(row.get("type")) not in SOLD_TYPES:
                continue
            name = _clip(row.get("product_name"), MenuItem, "name")
            if not name:
                self.report.skipped.append(_("#{id} — без названия", id=product_id))
                continue
            shown = self._product_shown(row)
            variants = self._variant_rows(product_id, row)
            if row.get("modifications"):
                priced = [variant for variant in variants if variant.price is not None]
                on_sale = [variant for variant in priced if variant.visible]
                price = min((variant.price for variant in on_sale or priced), default=None)
                shown = shown and bool(on_sale)
            else:
                price = self._price(row)
            if price is None:
                self.report.skipped.append(_("«{name}» — нет цены для этой точки", name=name))
                continue
            category_id = str(row.get("menu_category_id") or NO_CATEGORY)
            category = self.target.get(category_id) or self._no_category()
            if self.hidden_branch.get(category_id):
                shown = False
            item = self._item(product_id, name, category, price, _photo(row), shown)
            self._variants(item, variants)
            for entry in row.get("group_modifications") or []:
                if not isinstance(entry, dict) or _int(entry.get("is_deleted")):
                    continue
                group_id = str(entry.get("dish_modification_group_id") or "")
                if group_id:
                    groups.setdefault(group_id, (entry, []))[1].append(item)
        self._groups(groups)

    def _item(self, product_id, name, category, price, photo, visible):
        key, ref, obj = self._linked(Kind.PRODUCT, product_id, self.items)
        if obj is None:
            obj = MenuItem(
                partner_id=self.partner_id, category=category, name=name, price=price,
                image_url=photo, is_available=visible,
            )
            obj.save()
            self.items[obj.pk] = obj
            self.report.created[Kind.PRODUCT] += 1
        else:
            changed = self._assign(obj, {
                "name": name, "category": category, "price": price, "image_url": photo,
            })
            shown = self._shown_now(ref, obj, visible, obj.is_available)
            if obj.is_available != shown:
                obj.is_available = shown
                changed.append("is_available")
            self._save(obj, Kind.PRODUCT, changed)
        self._link(key, ref, obj, hidden=not visible)
        return obj

    def _variants(self, item, variants: list[_Variant]) -> None:
        for variant in variants:
            self.has_options = True
            if variant.price is None:
                continue
            key, ref, obj = self._linked(Kind.VARIANT, variant.key, self.variants)
            if obj is not None and obj.item_id != item.pk:
                obj = None
            if obj is None:
                obj = MenuItemVariant(
                    partner_id=self.partner_id, item=item, name=variant.name,
                    price=variant.price, is_available=variant.visible, sort_order=variant.order,
                )
                obj.save()
                self.variants[obj.pk] = obj
                self.report.created[Kind.VARIANT] += 1
            else:
                self._save(obj, Kind.VARIANT, self._assign(obj, {
                    "name": variant.name, "price": variant.price,
                    "is_available": variant.visible,
                }))
            self._link(key, ref, obj)

    # --- add-ons --------------------------------------------------------------

    def _groups(self, groups: dict) -> None:
        for group_id, (row, items) in groups.items():
            self.has_options = True
            lowest, highest = _int(row.get("num_min")), _int(row.get("num_max"))
            if str(row.get("type")) == ONE_OF:
                lowest = highest = 1
            if highest and lowest > highest:
                lowest = highest
            name = _clip(row.get("name"), ModifierGroup, "name") or f"#{group_id}"
            key, ref, obj = self._linked(Kind.MODIFIER_GROUP, group_id, self.groups)
            wanted = {item.pk for item in items}
            if obj is None:
                obj = ModifierGroup.objects.create(
                    partner_id=self.partner_id, name=name, min_choices=lowest,
                    max_choices=highest, is_active=True,
                )
                obj.items.set(wanted)
                self.groups[obj.pk] = obj
                self.report.created[Kind.MODIFIER_GROUP] += 1
            else:
                changed = self._assign(obj, {
                    "name": name, "min_choices": lowest, "max_choices": highest,
                    "is_active": True,
                })
                if set(obj.items.values_list("pk", flat=True)) != wanted:
                    obj.items.set(wanted)
                    if not changed:
                        self.report.updated[Kind.MODIFIER_GROUP] += 1
                self._save(obj, Kind.MODIFIER_GROUP, changed)
            self._link(key, ref, obj)
            self._modifiers(obj, group_id, row.get("modifications") or [])

    def _modifiers(self, group, group_id: str, rows) -> None:
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            modification_id = str(row.get("dish_modification_id") or "")
            name = _clip(row.get("name"), Modifier, "name")
            price = _hryvnias(row.get("price"))
            if not modification_id or not name or price is None:
                continue
            key, ref, obj = self._linked(
                Kind.MODIFIER, f"{group_id}:{modification_id}", self.modifiers,
            )
            if obj is None:
                obj = Modifier.objects.create(
                    partner_id=self.partner_id, group=group, name=name, price=price,
                    is_available=True, sort_order=_int(row.get("sort_order"), index) + 1,
                )
                self.modifiers[obj.pk] = obj
                self.report.created[Kind.MODIFIER] += 1
            else:
                self._save(obj, Kind.MODIFIER, self._assign(obj, {
                    "group": group, "name": name, "price": price, "is_available": True,
                }))
            self._link(key, ref, obj)

    # --- what Poster no longer has ---------------------------------------------------

    def _sweep(self) -> None:
        objects = {
            Kind.CATEGORY: (self.categories, "is_active"),
            Kind.PRODUCT: (self.items, "is_available"),
            Kind.VARIANT: (self.variants, "is_available"),
            Kind.MODIFIER_GROUP: (self.groups, "is_active"),
            Kind.MODIFIER: (self.modifiers, "is_available"),
        }
        for key, ref in list(self.refs.items()):
            if key in self.seen or ref.removed_in_poster:
                continue
            found, flag = objects[key[0]]
            obj = found.get(ref.object_id)
            if obj is not None and getattr(obj, flag):
                setattr(obj, flag, False)
                obj.save(update_fields=[flag, "updated_at"])
            ref.removed_in_poster = True
            ref.save(update_fields=["removed_in_poster", "updated_at"])
            self.report.removed[key[0]] += 1
