"""Языки текстов: каталог и подмены партнёра.

Тексты в коде пишутся по-русски и оборачиваются в ``_()``; переводы на
украинский и английский — в ``locale/<язык>/LC_MESSAGES/django.po``
(``infrastructure/scripts/messages.py``). Владелец заведения может заменить
любой текст на своём языке; подмены текущего обновления бота или задачи
лежат здесь, в контексте (``texts``), и сильнее каталога.

``_("Сумма: {total} грн", total=...)`` переводит и подставляет значения;
значения не экранируются — HTML экранирует вызывающий, как и раньше.
Модуль не импортирует доменные приложения: подмены ему передаёт адаптер.
"""

from __future__ import annotations

import string
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from asgiref.local import Local
from django.conf import settings
from django.utils import translation
from django.utils.functional import lazy

UK = "uk"
RU = "ru"
EN = "en"
LANGUAGES = (UK, RU, EN)
LANGUAGE_NAMES = {UK: "Українська", RU: "Русский", EN: "English"}
LANGUAGE_FLAGS = {UK: "🇺🇦", RU: "🇷🇺", EN: "🇬🇧"}

_state = Local()
_formatter = string.Formatter()


def normalize_language(code: str | None) -> str:
    """``"uk-UA"`` → ``"uk"``; пусто или неизвестный язык → ``""``."""

    short = (code or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return short if short in LANGUAGES else ""


def default_language() -> str:
    """Язык платформы по умолчанию: из ``LANGUAGE_CODE``, иначе украинский."""

    return normalize_language(settings.LANGUAGE_CODE) or UK


def active_language() -> str:
    return normalize_language(translation.get_language()) or default_language()


def placeholders(text: str) -> set[str]:
    """Имена подстановок ``{name}`` в тексте; для неверного шаблона — пусто."""

    try:
        return {name for _literal, name, _spec, _conv in _formatter.parse(text) if name}
    except ValueError:
        return set()


def _format(text: str, params: Mapping[str, object]) -> str:
    if not params:
        return text
    return text.format_map(params)


def gettext(message: str, /, **params: object) -> str:
    """Текст на активном языке: подмена партнёра, иначе каталог."""

    overrides = getattr(_state, "overrides", None)
    if overrides:
        custom = overrides.get((active_language(), message))
        if custom:
            try:
                return _format(custom, params)
            except (KeyError, ValueError, IndexError):
                # Сломанная подмена не должна ломать ответ гостю.
                pass
    if active_language() == RU:
        # Русский — сам текст в коде. Через каталог нельзя: недостающий
        # перевод Django берёт из LANGUAGE_CODE, и при `uk` на сервере
        # русский становился украинским.
        return _format(message, params)
    return _format(translation.gettext(message), params)


_ = gettext
gettext_lazy = lazy(gettext, str)


def N_(message: str) -> str:  # noqa: N802 — the usual gettext name
    """Mark a text for the catalog without translating it now (module constants)."""

    return message


def own_text(message: str) -> str:
    """The owner's own text for this catalog text in the active language, or ""."""

    overrides = getattr(_state, "overrides", None) or {}
    return overrides.get((active_language(), message), "")


def gettext_in(language: str, message: str, /, **params: object) -> str:
    with translation.override(normalize_language(language) or default_language()):
        return gettext(message, **params)


def variants(message: str) -> set[str]:
    """Текст на всех языках (с подменами контекста) — для сравнения с вводом."""

    return {gettext_in(language, message) for language in LANGUAGES}


@contextmanager
def texts(
    language: str, overrides: Mapping[tuple[str, str], str] | None = None
) -> Iterator[None]:
    """Активировать язык и подмены партнёра на время обновления или задачи."""

    previous = getattr(_state, "overrides", None)
    _state.overrides = dict(overrides or {})
    try:
        with translation.override(normalize_language(language) or default_language()):
            yield
    finally:
        _state.overrides = previous
