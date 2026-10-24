"""``{% tr "Текст {name}" name=value %}`` — a text of the catalog in templates.

The same catalog as ``_()`` in code (PQ-040, ``core/i18n.py``);
``infrastructure/scripts/messages.py`` collects ``{% tr "…" %}`` from
templates. The result is escaped like any template variable.
"""

from django import template

from core.i18n import gettext

register = template.Library()


@register.simple_tag
def tr(message: str, **params) -> str:
    return gettext(message, **params)
