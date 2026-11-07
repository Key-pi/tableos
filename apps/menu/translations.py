"""The catalog in the guest's language (catalog step 4, owner 2026-11-07).

The owner writes the catalog in the venue's main language (the model's own
fields) and may add a translation for each other guest language in Admin
(``translations``: ``{"en": {"name": "…", "description": "…"}}``). Guests see
their language where there is a translation and the original where there is
none. Staff, kitchen screens, bills for staff and Admin keep the original:
an order line stores its name in the main language (``item_name``), and a
guest's screen builds the line's name in the guest's language
(``line_names_in``).

Languages are the platform's guest languages (``core.i18n.LANGUAGES``): a
new one is a new key, nothing else changes.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from core.i18n import LANGUAGES, _, active_language

# The texts a guest reads, per model.
TRANSLATED_FIELDS = {
    "MenuCategory": ("name", "description"),
    "MenuBadge": ("name",),
    "MenuItem": ("name", "description"),
    "MenuItemAttribute": ("name", "value"),
    "MenuItemVariant": ("name",),
    "ModifierGroup": ("name",),
    "Modifier": ("name",),
}


def translation_languages(partner_id) -> tuple[str, ...]:
    """The guest languages a venue has on, except its main one: the owner
    writes the catalog in the main language and translates it into these
    (catalog step 4). Nothing while the venue is not known yet."""

    from apps.partners.models import GUEST_LANGUAGE_FIELDS, PartnerBotSettings

    if partner_id is None:
        return ()
    settings = PartnerBotSettings.objects.filter(partner_id=partner_id).first()
    if settings is None:
        return ()
    return tuple(
        code for code in LANGUAGES
        if code != settings.default_language and getattr(settings, GUEST_LANGUAGE_FIELDS[code])
    )


def fields_of(model) -> tuple[str, ...]:
    return TRANSLATED_FIELDS.get(model.__name__, ())


def _own(obj, language: str) -> dict:
    translations = getattr(obj, "translations", None) or {}
    entry = translations.get(language) if isinstance(translations, dict) else None
    return entry if isinstance(entry, dict) else {}


def translated(obj, field: str, language: str | None = None) -> str:
    """``field`` of ``obj`` in ``language`` (the reader's by default), or the
    original when there is no translation."""

    text = _own(obj, language or active_language()).get(field)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return getattr(obj, field)


def category_name(category, language: str | None = None) -> str:
    """``full_name`` in the reader's language: «Одяг › Футболки»."""

    name = translated(category, "name", language)
    if getattr(category, "parent_id", None):
        return f"{translated(category.parent, 'name', language)} › {name}"
    return name


def has_translation(obj, language: str) -> bool:
    return any(
        isinstance(text, str) and text.strip() for text in _own(obj, language).values()
    )


def clean_translations(obj) -> None:
    """Model validation: known languages and fields, text within the limit of
    the original field; empty entries are dropped. Errors are not tied to a
    field: Admin forms show translations as fields of their own."""

    raw = obj.translations or {}
    if not isinstance(raw, dict):
        raise ValidationError(_("Переводы — словарь по языкам."))
    fields = fields_of(type(obj))
    cleaned: dict = {}
    for language, entry in raw.items():
        if language not in LANGUAGES or not isinstance(entry, dict):
            raise ValidationError(_("Неизвестный язык перевода: {language}.", language=language))
        kept = {}
        for field, text in entry.items():
            if field not in fields or not isinstance(text, str):
                raise ValidationError(_("Это поле не переводится: {field}.", field=field))
            text = text.strip()
            limit = type(obj)._meta.get_field(field).max_length
            if limit and len(text) > limit:
                raise ValidationError(_("Перевод длиннее {limit} символов.", limit=limit))
            if text:
                kept[field] = text
        if kept:
            cleaned[language] = kept
    obj.translations = cleaned


def set_translation(obj, language: str, field: str, text: str) -> None:
    """Put one translation on ``obj`` (not saved)."""

    translations = dict(obj.translations or {})
    entry = dict(translations.get(language) or {})
    if text.strip():
        entry[field] = text.strip()
    else:
        entry.pop(field, None)
    if entry:
        translations[language] = entry
    else:
        translations.pop(language, None)
    obj.translations = translations


def line_names_in(lines, language: str | None = None) -> list[str]:
    """The names of cart or order lines in the guest's language, without
    queries (lines with ``menu_item`` and ``variant`` loaded).

    A line keeps its name in the main language (``item_name``); it is built
    again from its item and variant with their translations and from its
    add-ons as they were (``Choice.snapshot`` keeps their translations).
    When nothing of the line is translated, or the line no longer matches
    its item (renamed since, a variant removed), the stored name stays.
    """

    from apps.menu.options import line_name

    language = language or active_language()
    return [_line_name_in(line, language, line_name) for line in lines]


def line_name_in(line, language: str | None = None) -> str:
    return line_names_in([line], language)[0]


def _add_on_name(entry: dict, language: str) -> str:
    names = entry.get("names")
    text = names.get(language) if isinstance(names, dict) else None
    return text if isinstance(text, str) and text.strip() else str(entry.get("name", ""))


_NOT_LOADED = object()


def _related(line, name: str):
    """The line's item or variant if it is loaded; never a query (the bot
    renders in async code)."""

    if not getattr(line, f"{name}_id", None):
        return None
    descriptor = getattr(type(line), name, None)
    if hasattr(descriptor, "is_cached") and not descriptor.is_cached(line):
        return _NOT_LOADED
    return getattr(line, name)


def _line_name_in(line, language, line_name) -> str:
    item = _related(line, "menu_item")
    variant = _related(line, "variant")
    if item is None or item is _NOT_LOADED or variant is _NOT_LOADED:
        return line.item_name
    entries = [
        entry for entry in (getattr(line, "modifiers", None) or []) if isinstance(entry, dict)
    ]
    translated_add_on = any(_add_on_name(entry, language) != entry.get("name") for entry in entries)
    if not translated_add_on and not any(
        part is not None and has_translation(part, language) for part in (item, variant)
    ):
        return line.item_name
    originals = [str(entry.get("name", "")) for entry in entries]
    main = line_name(item.name, variant.name if variant is not None else "", originals)
    if main != line.item_name:
        return line.item_name
    return line_name(
        translated(item, "name", language),
        translated(variant, "name", language) if variant is not None else "",
        [_add_on_name(entry, language) for entry in entries],
    )


def add_on_names(modifier) -> dict:
    """An add-on's translations kept on the line (``Choice.snapshot``)."""

    return {
        language: entry["name"]
        for language, entry in (modifier.translations or {}).items()
        if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"].strip()
    }
