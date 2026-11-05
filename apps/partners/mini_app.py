"""Where the Telegram Mini App is offered (PQ-048).

The Mini App is a module of the venue (``PartnerBotSettings.module_webapp_enabled``,
with the cart, PQ-033); it opens at ``<WEBAPP_BASE_URL>/app/<bot>/``. Telegram
opens only HTTPS addresses, so without a public HTTPS address nothing is
offered and the bot works as before.
"""

from __future__ import annotations

import uuid

from django.conf import settings as django_settings

from apps.partners.languages import choose_language
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.partners.networks import serves_network
from core.i18n import N_, gettext_in

MENU_BUTTON_TEXT = N_("Меню и заказ")


def mini_app_url(bot_instance_id) -> str:
    base = (django_settings.WEBAPP_BASE_URL or "").rstrip("/")
    if not base.startswith("https://"):
        return ""
    return f"{base}/app/{uuid.UUID(str(bot_instance_id)).hex}/"


def _enabled(queryset) -> bool:
    return queryset.filter(module_webapp_enabled=True, module_cart_enabled=True).exists()


def mini_app_on(partner_id) -> bool:
    return _enabled(PartnerBotSettings.objects.filter(partner_id=partner_id))


def mini_app_link(bot_instance_id, partner_id, page: str = "") -> str:
    """The address to open for this venue (``page="staff/"``: the staff
    screen), or "" when not offered."""

    url = mini_app_url(bot_instance_id)
    return f"{url}{page}" if url and mini_app_on(partner_id) else ""


def menu_buttons(instance_ids) -> dict[str, tuple[str, str] | None]:
    """The chat menu button each bot should have: (address, text) or None.

    A network's bot offers it when one of its working venues has the module;
    the text is in the language of the bot's own venue."""

    buttons: dict[str, tuple[str, str] | None] = {}
    bots = BotInstance.objects.select_related("partner").filter(id__in=list(instance_ids))
    for bot in bots:
        url = mini_app_url(bot.id)
        if not url:
            buttons[str(bot.id)] = None
            continue
        if serves_network(bot):
            venues = PartnerBotSettings.objects.filter(
                partner__network_id=bot.partner.network_id,
                partner__status=Partner.Status.ACTIVE,
            )
        else:
            venues = PartnerBotSettings.objects.filter(partner_id=bot.partner_id)
        if not _enabled(venues):
            buttons[str(bot.id)] = None
            continue
        own = PartnerBotSettings.objects.filter(partner_id=bot.partner_id).first()
        buttons[str(bot.id)] = (url, gettext_in(choose_language(own), MENU_BUTTON_TEXT))
    return buttons
