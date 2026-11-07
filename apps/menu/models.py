import secrets
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from core.database.models import PartnerBoundModel
from core.i18n import _
from core.i18n import gettext_lazy as _lazy

MENU_PHOTO_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


def menu_photo_path(item: "MenuItem", filename: str) -> str:
    # A new name on every upload: the old link may stay in a guest's cache.
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"menu/{item.partner_id.hex}/{secrets.token_hex(8)}.{extension}"


def validate_menu_photo_size(photo) -> None:
    limit = settings.MENU_PHOTO_MAX_BYTES
    if photo.size > limit:
        raise ValidationError(
            _("Фото больше {size} МБ — уменьшите его.", size=limit // (1024 * 1024) or 1)
        )


def store_compressed_photo(instance, model, field: str = "image") -> str:
    """Before ``save``: a new upload is kept as a small WebP only
    (apps/menu/photos.py). Returns the name of a photo it replaces or that
    was cleared, to delete once the new one is saved."""

    photo = getattr(instance, field)
    previous = ""
    if instance.pk:
        previous = model.objects.filter(pk=instance.pk).values_list(field, flat=True).first() or ""
    if photo and not getattr(photo, "_committed", True):
        from apps.menu.photos import compress_menu_photo

        photo.save("photo.webp", compress_menu_photo(photo.file), save=False)
        return previous
    if not photo:
        return previous
    return ""


def delete_replaced_photo(instance, replaced: str, field: str = "image") -> None:
    photo = getattr(instance, field)
    if replaced and replaced != photo.name:
        # The old photo goes once the new one is saved.
        from django.db import transaction

        storage = photo.storage
        transaction.on_commit(lambda: storage.delete(replaced))


class TranslatableMixin:
    """Texts for guests in other languages (catalog step 4,
    ``apps/menu/translations.py``); the model's fields are the original, in
    the venue's main language."""

    def clean(self):
        super().clean()
        from apps.menu.translations import clean_translations

        clean_translations(self)


def translations_field():
    return models.JSONField(
        default=dict, blank=True, verbose_name=_lazy("Переводы"),
        help_text=_lazy("Тексты для гостей на других языках; заполняются в Admin."),
    )


class MenuCategory(TranslatableMixin, PartnerBoundModel):
    """Logical menu section used to group items inside a partner catalog.

    Two levels at most (owner, 2026-11-07): a category may sit inside a top
    category, never deeper. A schedule (from–until, on weekdays) shows the
    category all day but sells it only in its time (``apps/menu/catalog.py``).
    """

    class Station(models.TextChoices):
        # Where the category is made, for the kitchen/bar screens (PQ-048).
        ANY = "", _lazy("Не указано (на всех экранах)")
        BAR = "bar", _lazy("Бар")
        KITCHEN = "kitchen", _lazy("Кухня")

    name = models.CharField(max_length=255, verbose_name=_lazy("Название"))
    sort_order = models.PositiveIntegerField(default=100, verbose_name=_lazy("Порядок"))
    is_active = models.BooleanField(default=True, verbose_name=_lazy("Показывать"))
    station = models.CharField(
        max_length=16,
        choices=Station.choices,
        blank=True,
        default=Station.ANY,
        verbose_name=_lazy("Где готовят"),
        help_text=_lazy(
            "Для экранов кухни и бара: позиции категории видит экран этого места. "
            "Не указано — все экраны.",
        ),
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name=_lazy("Внутри категории"),
        help_text=_lazy("Подкатегория: например, «Футболки» внутри «Одежды». Два уровня."),
    )
    description = models.TextField(blank=True, verbose_name=_lazy("Описание"))
    available_from = models.TimeField(
        null=True, blank=True, verbose_name=_lazy("Продаётся с"),
        help_text=_lazy("Например, завтраки с 08:00 до 12:00. Пусто — весь день."),
    )
    available_until = models.TimeField(null=True, blank=True, verbose_name=_lazy("Продаётся до"))
    available_weekdays = models.JSONField(
        default=list, blank=True, verbose_name=_lazy("Дни продажи"),
        help_text=_lazy("Пусто — каждый день."),
    )
    translations = translations_field()

    class Meta:
        verbose_name = _lazy("категория меню")
        verbose_name_plural = _lazy("Категории меню")
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "name"],
                name="unique_menu_category_name_per_partner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.full_name}"

    @property
    def full_name(self) -> str:
        return f"{self.parent.name} › {self.name}" if self.parent_id else self.name

    @property
    def effective_station(self) -> str:
        """Where the category is made; a subcategory without its own place
        takes its parent's."""

        if self.station or not self.parent_id:
            return self.station
        return self.parent.station

    def clean(self):
        super().clean()
        if self.parent_id:
            parent = self.parent
            if parent.pk == self.pk:
                raise ValidationError({"parent": _("Категория не может быть внутри себя.")})
            if parent.partner_id != self.partner_id:
                raise ValidationError({"parent": _("Это категория другого заведения.")})
            if parent.parent_id:
                raise ValidationError(
                    {"parent": _("Подкатегории — только два уровня: выберите верхнюю категорию.")}
                )
            if self.pk and MenuCategory.objects.filter(parent_id=self.pk).exists():
                raise ValidationError(
                    {"parent": _("У этой категории есть свои подкатегории — её нельзя вложить.")}
                )
        if (self.available_from is None) != (self.available_until is None):
            raise ValidationError(
                {"available_until": _("Укажите и «с», и «до» — или оставьте оба пустыми.")}
            )
        if self.available_from is not None and self.available_from == self.available_until:
            raise ValidationError({"available_until": _("«С» и «до» не могут совпадать.")})
        days = self.available_weekdays or []
        if not isinstance(days, list) or any(day not in range(7) for day in days):
            raise ValidationError({"available_weekdays": _("Дни недели — числа от 0 до 6.")})


class MenuBadge(TranslatableMixin, PartnerBoundModel):
    """An owner's own label for items, next to the ready ones
    (``apps/menu/catalog.py:BUILTIN_BADGES``)."""

    name = models.CharField(max_length=40, verbose_name=_lazy("Название"))
    emoji = models.CharField(max_length=8, blank=True, verbose_name=_lazy("Значок"))
    sort_order = models.PositiveIntegerField(default=100, verbose_name=_lazy("Порядок"))
    translations = translations_field()

    class Meta:
        verbose_name = _lazy("метка позиций")
        verbose_name_plural = _lazy("Метки позиций")
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["partner", "name"], name="unique_menu_badge_name")
        ]

    def __str__(self) -> str:
        return f"{self.emoji} {self.name}".strip()


class MenuItem(TranslatableMixin, PartnerBoundModel):
    """Sellable menu position shown to guests and reused in carts and orders."""

    class ItemType(models.TextChoices):
        INTERNAL = "internal", _lazy("Своя позиция")
        PARTNER = "partner", _lazy("Позиция партнёра")

    category = models.ForeignKey(
        MenuCategory,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_lazy("Категория"),
    )
    public_id = models.CharField(max_length=12, editable=False, verbose_name=_lazy("Код позиции"))
    name = models.CharField(max_length=255, verbose_name=_lazy("Название"))
    description = models.TextField(blank=True, verbose_name=_lazy("Описание"))
    translations = translations_field()
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name=_lazy("Цена"),
    )
    image = models.ImageField(
        upload_to=menu_photo_path,
        blank=True,
        validators=[
            FileExtensionValidator(MENU_PHOTO_EXTENSIONS),
            validate_menu_photo_size,
        ],
        verbose_name=_lazy("Фото"),
        help_text=_lazy(
            "JPG, PNG или WebP до 5 МБ; сохраняется уменьшенным (WebP, до 1200 px). "
            "Без фото берётся ссылка ниже.",
        ),
    )
    image_url = models.URLField(blank=True, verbose_name=_lazy("Ссылка на фото"))
    item_type = models.CharField(
        max_length=16,
        choices=ItemType.choices,
        default=ItemType.INTERNAL,
        verbose_name=_lazy("Тип"),
    )
    is_available = models.BooleanField(default=True, verbose_name=_lazy("В наличии"))
    # Ordering channels the item is sold in (ECOSYSTEM_DIRECTION.md): a hookah
    # may be table-only, a bottled drink pickup-only.
    available_at_table = models.BooleanField(default=True, verbose_name=_lazy("За столом"))
    available_for_pickup = models.BooleanField(default=True, verbose_name=_lazy("Самовывоз"))
    available_for_delivery = models.BooleanField(default=True, verbose_name=_lazy("Доставка"))
    available_for_shipping = models.BooleanField(default=True, verbose_name=_lazy(
        "Отправка почтой",
    ))
    sort_order = models.PositiveIntegerField(default=100, verbose_name=_lazy("Порядок"))

    # The showcase (module_showcase_enabled, owner 2026-11-07): shown to
    # guests only while the module is on.
    class PortionUnit(models.TextChoices):
        GRAM = "g", _lazy("г")
        KILOGRAM = "kg", _lazy("кг")
        MILLILITRE = "ml", _lazy("мл")
        LITRE = "l", _lazy("л")
        PIECE = "pcs", _lazy("шт")
        CENTIMETRE = "cm", _lazy("см")
        MINUTE = "min", _lazy("мин")

    portion = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.001"))],
        verbose_name=_lazy("Порция"),
        help_text=_lazy("Объём, вес или размер: 300 мл, 250 г, 60 мин."),
    )
    portion_unit = models.CharField(
        max_length=8, choices=PortionUnit.choices, blank=True, verbose_name=_lazy("Единица"),
    )
    old_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name=_lazy("Старая цена"),
        help_text=_lazy("Показывается зачёркнутой рядом с ценой; должна быть выше цены."),
    )
    badges = models.JSONField(default=list, blank=True, verbose_name=_lazy("Метки"))
    custom_badges = models.ManyToManyField(
        MenuBadge, blank=True, related_name="items", verbose_name=_lazy("Свои метки"),
    )
    sku = models.CharField(max_length=64, blank=True, verbose_name=_lazy("Артикул"))
    barcode = models.CharField(max_length=64, blank=True, verbose_name=_lazy("Штрихкод"))
    # Stock (catalog step 3, module «Остатки»): counted only with the tick;
    # an item with variants counts each variant (MenuItemVariant.stock_quantity).
    track_stock = models.BooleanField(
        default=False, verbose_name=_lazy("Учитывать остаток"),
        help_text=_lazy("Заказ списывает при оформлении, отмена возвращает. У позиции с "
                        "вариантами остаток — у каждого варианта."),
    )
    stock_quantity = models.IntegerField(
        default=0, verbose_name=_lazy("Остаток"),
        help_text=_lazy("Сколько есть сейчас. Меньше нуля — продано на кассе больше, чем "
                        "по учёту: поправьте."),
    )

    CHANNEL_FIELDS = {
        "table": "available_at_table",
        "pickup": "available_for_pickup",
        "delivery": "available_for_delivery",
        "shipping": "available_for_shipping",
    }

    class Meta:
        verbose_name = _lazy("позиция меню")
        verbose_name_plural = _lazy("Позиции меню")
        ordering = ["category__sort_order", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "public_id"],
                name="unique_menu_item_public_id_per_partner",
            ),
            models.CheckConstraint(
                condition=Q(price__gt=0),
                name="menu_item_price_positive",
            ),
            models.UniqueConstraint(
                fields=["partner", "sku"], condition=~Q(sku=""), name="unique_menu_item_sku",
                violation_error_message=_lazy("Такой артикул уже есть у другой позиции."),
            ),
            models.UniqueConstraint(
                fields=["partner", "barcode"], condition=~Q(barcode=""),
                name="unique_menu_item_barcode",
                violation_error_message=_lazy("Такой штрихкод уже есть у другой позиции."),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.name}"

    @property
    def photo_url(self) -> str:
        """The uploaded photo, else the link (PQ-048)."""

        if self.image:
            return self.image.url
        return self.image_url

    def is_sold_in(self, channel: str) -> bool:
        return bool(getattr(self, self.CHANNEL_FIELDS[channel]))

    def clean(self):
        super().clean()
        if self.old_price is not None and self.price is not None and self.old_price <= self.price:
            raise ValidationError({"old_price": _("Старая цена должна быть выше цены.")})
        if (self.portion is None) != (not self.portion_unit):
            raise ValidationError(
                {"portion_unit": _("Укажите и порцию, и единицу — или оставьте оба пустыми.")}
            )
        from apps.menu.catalog import BUILTIN_BADGES

        badges = self.badges or []
        if not isinstance(badges, list) or any(code not in BUILTIN_BADGES for code in badges):
            raise ValidationError({"badges": _("Неизвестная метка.")})

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = secrets.token_hex(4)
        replaced = store_compressed_photo(self, MenuItem)
        result = super().save(*args, **kwargs)
        delete_replaced_photo(self, replaced)
        return result


class MenuItemPhoto(PartnerBoundModel):
    """More photos of an item (showcase): the gallery after the main one."""

    item = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name="photos", verbose_name=_lazy("Позиция"),
    )
    image = models.ImageField(
        upload_to=menu_photo_path,
        validators=[FileExtensionValidator(MENU_PHOTO_EXTENSIONS), validate_menu_photo_size],
        verbose_name=_lazy("Фото"),
        help_text=_lazy("JPG, PNG или WebP до 5 МБ; сохраняется уменьшенным."),
    )
    sort_order = models.PositiveIntegerField(default=100, verbose_name=_lazy("Порядок"))

    class Meta:
        verbose_name = _lazy("фото позиции")
        verbose_name_plural = _lazy("Ещё фото")
        ordering = ["sort_order", "created_at"]

    def __str__(self) -> str:
        return self.image.name

    def save(self, *args, **kwargs):
        if self.item_id and not self.partner_id:
            self.partner_id = self.item.partner_id
        replaced = store_compressed_photo(self, MenuItemPhoto)
        result = super().save(*args, **kwargs)
        delete_replaced_photo(self, replaced)
        return result


class MenuItemAttribute(TranslatableMixin, PartnerBoundModel):
    """A characteristic of an item (showcase): brand, material, composition,
    allergens, calories — name and value as the owner writes them."""

    item = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name="attributes",
        verbose_name=_lazy("Позиция"),
    )
    name = models.CharField(max_length=80, verbose_name=_lazy("Характеристика"))
    value = models.CharField(max_length=255, verbose_name=_lazy("Значение"))
    translations = translations_field()
    sort_order = models.PositiveIntegerField(default=100, verbose_name=_lazy("Порядок"))

    class Meta:
        verbose_name = _lazy("характеристика")
        verbose_name_plural = _lazy("Характеристики")
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return f"{self.name}: {self.value}"

    def save(self, *args, **kwargs):
        if self.item_id and not self.partner_id:
            self.partner_id = self.item.partner_id
        return super().save(*args, **kwargs)


# --- variants and add-ons (catalog step 2, module_options_enabled) ---------------


class MenuItemVariant(TranslatableMixin, PartnerBoundModel):
    """A variant of an item with its own price: a size, colour or pack
    («250 мл», «M, червона», «500 г»). One list per item (owner, 2026-11-07);
    while the item has variants on sale the guest picks one."""

    item = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name="variants",
        verbose_name=_lazy("Позиция"),
    )
    name = models.CharField(
        max_length=80, verbose_name=_lazy("Вариант"),
        help_text=_lazy("Как увидит гость: 250 мл, M, червона, 500 г."),
    )
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))],
        verbose_name=_lazy("Цена"),
    )
    is_available = models.BooleanField(default=True, verbose_name=_lazy("В наличии"))
    sort_order = models.PositiveIntegerField(default=100, verbose_name=_lazy("Порядок"))
    translations = translations_field()
    stock_quantity = models.IntegerField(
        default=0, verbose_name=_lazy("Остаток"),
        help_text=_lazy("Когда у позиции включено «Учитывать остаток»."),
    )

    class Meta:
        verbose_name = _lazy("вариант позиции")
        verbose_name_plural = _lazy("Варианты")
        ordering = ["sort_order", "price", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gt=0), name="menu_item_variant_price_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item.name}, {self.name}"

    def save(self, *args, **kwargs):
        if self.item_id and not self.partner_id:
            self.partner_id = self.item.partner_id
        return super().save(*args, **kwargs)


class ModifierGroup(TranslatableMixin, PartnerBoundModel):
    """A group of add-ons set once and attached to items or whole categories
    (owner, 2026-11-07): «Сиропы» (up to 3), «Молоко» (one, required),
    «Без…». A category's group also covers its subcategories."""

    name = models.CharField(
        max_length=80, verbose_name=_lazy("Название"),
        help_text=_lazy("Как увидит гость: Сироп, Молоко, Соус, Убрать."),
    )
    min_choices = models.PositiveSmallIntegerField(
        default=0, verbose_name=_lazy("Выбрать не меньше"),
        help_text=_lazy("0 — по желанию; 1 и больше — без выбора не добавить."),
    )
    max_choices = models.PositiveSmallIntegerField(
        default=1, verbose_name=_lazy("Выбрать не больше"),
        help_text=_lazy("1 — один из списка; 0 — сколько угодно."),
    )
    is_active = models.BooleanField(default=True, verbose_name=_lazy("Показывать"))
    translations = translations_field()
    sort_order = models.PositiveIntegerField(default=100, verbose_name=_lazy("Порядок"))
    items = models.ManyToManyField(
        MenuItem, blank=True, related_name="modifier_groups", verbose_name=_lazy("Позиции"),
    )
    categories = models.ManyToManyField(
        MenuCategory, blank=True, related_name="modifier_groups",
        verbose_name=_lazy("Категории"),
        help_text=_lazy("Группа для всех позиций категории и её подкатегорий."),
    )

    class Meta:
        verbose_name = _lazy("группа добавок")
        verbose_name_plural = _lazy("Группы добавок")
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        if self.max_choices and self.min_choices > self.max_choices:
            raise ValidationError(
                {"min_choices": _("«Не меньше» не может быть больше, чем «не больше».")}
            )


class Modifier(TranslatableMixin, PartnerBoundModel):
    """One add-on of a group: «Карамельний сироп» +15 грн, «Без цибулі» 0 грн."""

    group = models.ForeignKey(
        ModifierGroup, on_delete=models.CASCADE, related_name="modifiers",
        verbose_name=_lazy("Группа"),
    )
    name = models.CharField(max_length=80, verbose_name=_lazy("Добавка"))
    translations = translations_field()
    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_lazy("Доплата"), help_text=_lazy("0 — бесплатно."),
    )
    is_available = models.BooleanField(default=True, verbose_name=_lazy("В наличии"))
    sort_order = models.PositiveIntegerField(default=100, verbose_name=_lazy("Порядок"))

    class Meta:
        verbose_name = _lazy("добавка")
        verbose_name_plural = _lazy("Добавки")
        ordering = ["sort_order", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0), name="modifier_price_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.group.name}: {self.name}"

    def save(self, *args, **kwargs):
        if self.group_id and not self.partner_id:
            self.partner_id = self.group.partner_id
        return super().save(*args, **kwargs)


class StockMovement(PartnerBoundModel):
    """One change of an item's or variant's stock (catalog step 3): who or
    what changed it and what is left. The source is named by its type and id
    (an order, a quick sale, a refund), as in the bonus journal, so the menu
    does not depend on orders and billing."""

    class Reason(models.TextChoices):
        ORDER = "order", _lazy("Заказ")
        CANCEL = "cancel", _lazy("Отмена заказа")
        QUICK_SALE = "quick_sale", _lazy("Быстрая продажа")
        REFUND = "refund", _lazy("Возврат на склад")
        COUNT = "count", _lazy("Пересчёт в Admin")

    item = models.ForeignKey(
        MenuItem, on_delete=models.CASCADE, related_name="stock_movements",
        verbose_name=_lazy("Позиция"),
    )
    # A deleted variant takes its movements along: what an order took of it
    # cannot go back to the item's own stock by mistake.
    variant = models.ForeignKey(
        MenuItemVariant, on_delete=models.CASCADE, null=True, blank=True,
        related_name="stock_movements", verbose_name=_lazy("Вариант"),
    )
    delta = models.IntegerField(verbose_name=_lazy("Изменение"))
    balance_after = models.IntegerField(verbose_name=_lazy("Остаток после"))
    reason = models.CharField(max_length=16, choices=Reason.choices, verbose_name=_lazy("Причина"))
    source_type = models.CharField(max_length=32, blank=True, verbose_name=_lazy("Источник"))
    source_id = models.CharField(max_length=64, blank=True, verbose_name=_lazy("Номер источника"))
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=_lazy("Кто"),
    )

    class Meta:
        verbose_name = _lazy("движение остатка")
        verbose_name_plural = _lazy("Движение остатков")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["source_type", "source_id"])]

    def __str__(self) -> str:
        return f"{self.item.name}: {self.delta:+d}"
