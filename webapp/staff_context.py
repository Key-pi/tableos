"""Who opens the staff screen of the Mini App and for which venue (PQ-048, stage 2).

As in the bot's work mode: an active staff profile of this venue linked to
the Telegram account, its active user of the same venue, checked on every
request (``apps/employees/selectors.py:get_staff_employee_by_telegram``).
A network's bot serves every venue where the person works; the screen sends
the venue it shows (``?point=``), the server checks it again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from apps.employees.models import EmployeeProfile
from apps.employees.selectors import get_staff_employee_by_telegram
from apps.partners.mini_app import staff_screen_on_for
from apps.partners.models import BotInstance, Partner
from apps.partners.networks import network_points, serves_network
from bot.services.content import BotContent
from bot.services.language import language_context, partner_bot_settings
from core.i18n import N_
from webapp.auth import InitDataError, TelegramUser, validate_init_data
from webapp.context import INIT_DATA_HEADER, REOPEN, WebAppDenied, find_bot

NOT_STAFF = N_("Рабочий экран — только для сотрудников этого заведения.")


@dataclass
class StaffRequest:
    bot: BotInstance
    user: TelegramUser
    partner: Partner
    employee: EmployeeProfile
    content: BotContent
    venues: list = field(default_factory=list)  # where the person works in this bot
    language: str = ""
    overrides: dict = field(default_factory=dict)


def _employee(partner_id, telegram_id: int) -> EmployeeProfile | None:
    try:
        return get_staff_employee_by_telegram(partner_id, telegram_id)
    except EmployeeProfile.DoesNotExist:
        return None


def _venues(bot: BotInstance, telegram_id: int) -> list[tuple[Partner, EmployeeProfile]]:
    if not serves_network(bot):
        candidates = [bot.partner]
    else:
        candidates = network_points(bot.partner.network_id)
    venues = []
    for partner in candidates:
        employee = _employee(partner.id, telegram_id)
        if employee is not None:
            venues.append((partner, employee))
    return venues


def staff_request(request, bot_hex: str) -> StaffRequest:
    bot = find_bot(bot_hex)
    try:
        user = validate_init_data(request.headers.get(INIT_DATA_HEADER, ""), bot.token)
    except InitDataError as exc:
        raise WebAppDenied(401, REOPEN, code="unauthorized") from exc

    venues = _venues(bot, user.id)
    chosen = None
    try:
        wanted = uuid.UUID(hex=request.GET.get("point", ""))
    except ValueError:
        wanted = None
    for partner, employee in venues:
        if wanted is None or partner.id == wanted:
            chosen = (partner, employee)
            break
    if chosen is None and venues:
        chosen = venues[0]

    def deny(status: int, message: str, code: str) -> WebAppDenied:
        denied = WebAppDenied(status, message, code=code)
        denied.language, denied.overrides = language, overrides
        return denied

    if chosen is None:
        language, overrides = language_context(bot.partner, user.id, user.language_code)
        raise deny(403, NOT_STAFF, "not_staff")
    partner, employee = chosen
    # The screen asks every 8 s: the language is worked out once, and the
    # staff profile just loaded gives the person's own choice.
    language, overrides = language_context(
        partner, user.id, user.language_code, staff_choice=employee.user.language
    )
    if partner.status != Partner.Status.ACTIVE:
        raise deny(403, N_("Заведение сейчас не принимает заказы."), "suspended")
    if not staff_screen_on_for(partner_bot_settings(partner)):
        raise deny(
            403,
            N_("Рабочий экран здесь пока не включён — работайте в чате бота."),
            "module_off",
        )
    return StaffRequest(
        bot=bot, user=user, partner=partner, employee=employee,
        content=BotContent.for_partner(partner),
        venues=[venue for venue, _employee_profile in venues],
        language=language, overrides=overrides,
    )
