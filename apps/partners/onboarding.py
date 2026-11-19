"""A business in one command (PQ-045, owner 2026-11-04 and 2026-11-19).

The venue, its bot settings (every module off, PQ-053), its bot and its
owner — a login of their own with Admin and the bot, no password — in one
transaction; the business works at once (``Partner.Status.ACTIVE``) and the
platform owner gets a message through the platform bot after the commit.
The token is checked with Telegram (``getMe``) before anything is written:
no network inside the transaction.

Adapters: ``create_business`` management command now; the Botera Hub API
later (owner 2026-11-19: "потом с хабом сделаем") calls the same function.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils.text import slugify

from apps.employees.models import EmployeeProfile
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, TelegramAccount, User
from core.i18n import gettext as _


class BusinessError(Exception):
    """The business was not created; the message says why, for a person."""


@dataclass(frozen=True)
class NewBusiness:
    partner: Partner
    bot: BotInstance
    owner: User


def fetch_bot_username(token: str) -> str:
    """Ask Telegram which bot a token belongs to (``getMe``)."""

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise BusinessError(
            _("Telegram не принял токен бота (HTTP {code}): проверьте его в @BotFather.",
              code=exc.code)
        ) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise BusinessError(
            _("Не удалось связаться с Telegram, чтобы проверить токен бота. Попробуйте ещё раз.")
        ) from exc
    username = (payload.get("result") or {}).get("username") or ""
    if not payload.get("ok") or not username:
        raise BusinessError(_("Telegram не принял токен бота: проверьте его в @BotFather."))
    return username


def admin_link(partner: Partner) -> str:
    """The venue's page in Admin; absolute when the site's address is known."""

    path = reverse("admin:partners_partner_change", args=[partner.id])
    return f"{settings.WEBAPP_BASE_URL}{path}" if settings.WEBAPP_BASE_URL else path


def _connected_bot(username: str) -> BotInstance | None:
    return BotInstance.objects.select_related("partner").filter(username__iexact=username).first()


def _free_slug(name: str, username: str) -> str:
    # A Cyrillic name gives no Latin code: the bot's name is Latin and unique.
    base = (slugify(name) or slugify(username) or "business")[:40]
    slug, number = base, 1
    while Partner.objects.filter(slug=slug).exists():
        number += 1
        slug = f"{base}-{number}"
    return slug


def _checked(*, name, bot_token, owner_telegram_id, timezone_name) -> tuple[str, str]:
    name = (name or "").strip()
    if not name or len(name) > Partner._meta.get_field("name").max_length:
        raise BusinessError(_("Укажите название бизнеса (до 255 знаков)."))
    bot_token = (bot_token or "").strip()
    if not BotInstance.looks_like_valid_token(bot_token):
        raise BusinessError(_("Это не похоже на токен бота из @BotFather."))
    if not isinstance(owner_telegram_id, int) or owner_telegram_id <= 0:
        raise BusinessError(_("Telegram ID владельца — положительное число."))
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise BusinessError(_("Неизвестный часовой пояс: {name}.", name=timezone_name)) from exc
    return name, bot_token


def create_business(
    *,
    name: str,
    bot_token: str,
    owner_telegram_id: int,
    owner_name: str = "",
    phone: str = "",
    timezone_name: str = "Europe/Kyiv",
) -> NewBusiness:
    name, bot_token = _checked(name=name, bot_token=bot_token,
                               owner_telegram_id=owner_telegram_id, timezone_name=timezone_name)
    username = BotInstance.normalize_username(fetch_bot_username(bot_token))
    taken = _connected_bot(username)
    if taken is not None:
        raise BusinessError(_("Бот @{bot} уже подключён к «{venue}».",
                              bot=taken.username, venue=taken.partner.name))
    owner_name = (owner_name or "").strip()
    try:
        with transaction.atomic():
            partner = Partner.objects.create(
                name=name, slug=_free_slug(name, username), timezone=timezone_name,
                contact_phone=(phone or "").strip(), status=Partner.Status.ACTIVE,
            )
            PartnerBotSettings.objects.create(partner=partner)
            bot = BotInstance(partner=partner, display_name=name, username=username,
                              mode=BotInstance.Mode.POLLING)
            bot.set_token(bot_token)
            bot.save()
            account, _created = TelegramAccount.objects.get_or_create(
                telegram_id=owner_telegram_id, defaults={"first_name": owner_name},
            )
            # A login per business, as for invited staff: the same person may
            # own another business with its own login.
            owner = User(
                username=f"tg{owner_telegram_id}-{partner.id.hex[:8]}", partner=partner,
                role=User.Role.OWNER, first_name=owner_name or account.first_name,
                last_name="" if owner_name else account.last_name,
            )
            owner.set_unusable_password()
            owner.save()
            EmployeeProfile.objects.create(partner=partner, user=owner, telegram_account=account)
            AdminAccessProfile.objects.create(
                user=owner, partner=partner, preset=AdminAccessPreset.OWNER,
                is_partner_owner=True, can_access_admin=True,
            )
            from apps.notifications.services import tell_platform_about_new_business

            tell_platform_about_new_business(partner.id)
    except IntegrityError as exc:
        # Another command took the bot (or the code) between the check and the save.
        raise BusinessError(_("Бот @{bot} уже подключён.", bot=username)) from exc
    owner.refresh_from_db()
    return NewBusiness(partner=partner, bot=bot, owner=owner)
