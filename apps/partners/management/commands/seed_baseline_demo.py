"""Demo data for local testing: two venues, staff, tables, menu, bonuses.

Every option also reads an environment variable (see ``.env.example``), so the
Docker ``init`` service seeds from ``.env`` with no arguments:

- ``DEMO_CAFE_BOT_TOKEN`` / ``DEMO_HOOKAH_BOT_TOKEN`` — BotFather tokens; the
  bot username is looked up with ``getMe`` unless ``DEMO_*_BOT_USERNAME`` is
  set. Without a token the venue's bot stays inactive.
- ``DEMO_TELEGRAM_ID`` — your own Telegram user id: it becomes the first owner
  of both venues (``/staff`` works for you) and gets start bonuses as a guest.

``--keep-existing`` (used by Docker on every start) creates a venue only when
it is missing and otherwise only applies bot tokens and your Telegram binding,
so changes made in Admin survive a restart. Without it the demo catalogue is
reset to these defaults. ``docker compose down -v`` wipes everything.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.analytics.models import TrafficSource
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.services import assert_bonus_balance_reconciled, sync_bonus_balance_from_ledger
from apps.employees.models import EmployeeProfile
from apps.menu.models import MenuCategory, MenuItem
from apps.partners.models import BotInstance, OpeningHours, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, GuestProfile, TelegramAccount
from core.security.environment import is_production

HOOKAH_SLUG = "night-owl-hookah"
CAFE_SLUG = "riverstone-cafe"
START_BONUS_KEY = "demo-seed:start-bonus"


@dataclass(frozen=True)
class StaffSeed:
    username: str
    email: str
    first_name: str
    last_name: str
    role: str
    title: str
    hourly_rate: Decimal
    commission_rate: Decimal
    telegram_id: int
    telegram_username: str


@dataclass(frozen=True)
class TableSeed:
    number: int
    name: str
    seats: int
    qr_token: str


@dataclass(frozen=True)
class MenuItemSeed:
    public_id: str
    name: str
    price: Decimal
    sort_order: int
    description: str = ""


@dataclass(frozen=True)
class VenueSeed:
    slug: str
    name: str
    phone: str
    bot_username: str
    bot_display_name: str
    token_option: str
    username_option: str
    env_name: str


VENUES = (
    VenueSeed(
        slug=HOOKAH_SLUG,
        name="Night Owl Hookah Lounge",
        phone="+380671110101",
        bot_username="night_owl_hookah_bot",
        bot_display_name="Night Owl Hookah Bot",
        token_option="hookah_bot_token",
        username_option="hookah_bot_username",
        env_name="DEMO_HOOKAH_BOT_TOKEN",
    ),
    VenueSeed(
        slug=CAFE_SLUG,
        name="Riverstone Cafe",
        phone="+380672220202",
        bot_username="riverstone_cafe_bot",
        bot_display_name="Riverstone Cafe Bot",
        token_option="cafe_bot_token",
        username_option="cafe_bot_username",
        env_name="DEMO_CAFE_BOT_TOKEN",
    ),
)

# The owner whose Telegram is replaced by DEMO_TELEGRAM_ID in each venue.
TESTER_OWNER_USERNAME = {HOOKAH_SLUG: "smoke_owner_1", CAFE_SLUG: "cafe_owner_1"}

def _hours_text(partner: Partner, module: str) -> str:
    from apps.partners.opening_hours import intervals_for

    hours = intervals_for(partner, module)
    if not hours:
        return "открыто всегда"
    return f"ежедневно {hours[0].opens_at:%H:%M}–{hours[0].closes_at:%H:%M}"


def _payment_options(settings: PartnerBotSettings, module: str) -> str:
    options = []
    if getattr(settings, f"{module}_pay_transfer"):
        options.append("переводом заранее")
    if getattr(settings, f"{module}_pay_on_receipt"):
        options.append("при получении")
    return " или ".join(options) or "не настроена"


TRANSFER_DETAILS = (
    "Карта 4149 0000 0000 0000 (демо), получатель ФОП Демо Демо. "
    "В комментарии к переводу укажите номер заказа."
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def fetch_bot_username(token: str) -> str:
    """Ask Telegram which bot a token belongs to (``getMe``)."""

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise CommandError(
            f"Telegram не принял токен бота (HTTP {exc.code}): проверьте его в @BotFather."
        ) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise CommandError(
            "Не удалось связаться с Telegram, чтобы узнать имя бота по токену "
            f"({exc}). Проверьте интернет или задайте имя бота в DEMO_*_BOT_USERNAME."
        ) from exc
    username = (payload.get("result") or {}).get("username") or ""
    if not payload.get("ok") or not username:
        raise CommandError("Telegram не принял токен бота: проверьте его в @BotFather.")
    return username


class Command(BaseCommand):
    help = (
        "Демо-данные для локальной проверки: два заведения (кальянная и кафе), персонал, "
        "столы, меню, бонусы, самовывоз; токены ботов и ваш Telegram ID — из .env."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--admin-username", default=_env("DEMO_ADMIN_USERNAME", "platform_admin")
        )
        parser.add_argument("--admin-email", default="admin@botera.local")
        parser.add_argument("--admin-password", default=_env("DEMO_ADMIN_PASSWORD", "botera12345"))
        parser.add_argument(
            "--staff-password",
            default=_env("DEMO_STAFF_PASSWORD", "botera12345"),
            help="Пароль всех сотрудников демо-заведений.",
        )
        parser.add_argument("--hookah-bot-token", default=_env("DEMO_HOOKAH_BOT_TOKEN"))
        parser.add_argument("--hookah-bot-username", default=_env("DEMO_HOOKAH_BOT_USERNAME"))
        parser.add_argument("--cafe-bot-token", default=_env("DEMO_CAFE_BOT_TOKEN"))
        parser.add_argument("--cafe-bot-username", default=_env("DEMO_CAFE_BOT_USERNAME"))
        parser.add_argument(
            "--telegram-id",
            default=_env("DEMO_TELEGRAM_ID"),
            help="Ваш Telegram ID: станет владельцем обоих заведений и получит бонусы.",
        )
        parser.add_argument("--telegram-username", default=_env("DEMO_TELEGRAM_USERNAME"))
        parser.add_argument(
            "--start-bonuses",
            default=_env("DEMO_START_BONUSES", "500"),
            help="Бонусы на ваш гостевой профиль в каждом заведении (один раз).",
        )
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help=(
                "Не трогать уже созданные заведения (меню, столы, персонал, пароли): "
                "только подключить ботов и ваш Telegram. Так Docker запускает сид при "
                "каждом старте."
            ),
        )
        parser.add_argument(
            "--public-url", default=_env("DEMO_PUBLIC_URL", "http://localhost:8000")
        )

    def handle(self, *args, **options):
        if is_production():
            # Demo accounts have known passwords (AA-038).
            raise CommandError("Демо-данные не загружаются в продакшене.")
        tester_id = self._parse_telegram_id(options["telegram_id"])
        try:
            start_bonuses = Decimal(str(options["start_bonuses"] or "0")).quantize(Decimal("0.01"))
        except ArithmeticError as exc:
            raise CommandError("--start-bonuses должно быть числом.") from exc
        if start_bonuses < 0:
            raise CommandError("--start-bonuses не может быть отрицательным.")

        # Telegram lookups happen before the transaction: no network I/O while
        # the database transaction is open.
        bots = self._resolve_bots(options)

        with transaction.atomic():
            report = self._seed(
                options=options,
                bots=bots,
                tester_id=tester_id,
                tester_username=options["telegram_username"].removeprefix("@"),
                start_bonuses=start_bonuses,
            )
        self._print_report(report, options)

    # ------------------------------------------------------------------ input

    @staticmethod
    def _parse_telegram_id(raw) -> int | None:
        raw = str(raw or "").strip()
        if not raw:
            return None
        if not raw.isdigit() or int(raw) <= 0:
            raise CommandError(
                "DEMO_TELEGRAM_ID должен быть числом — ваш Telegram ID (узнать: @userinfobot)."
            )
        return int(raw)

    def _resolve_bots(self, options) -> dict[str, tuple[str, str]]:
        """Venue slug → (token, bot username) for every token given."""

        bots: dict[str, tuple[str, str]] = {}
        for venue in VENUES:
            token = (options[venue.token_option] or "").strip()
            if not token:
                continue
            if not BotInstance.looks_like_valid_token(token):
                raise CommandError(
                    f"{venue.env_name}: это не похоже на токен бота (вид 123456:ABC...)."
                )
            username = BotInstance.normalize_username(options[venue.username_option] or "")
            if not username:
                username = fetch_bot_username(token)
            bots[venue.slug] = (token, username)
        tokens = [token for token, _ in bots.values()]
        if len(tokens) != len(set(tokens)):
            raise CommandError(
                "Один и тот же токен указан для двух заведений: у каждого заведения свой бот."
            )
        return bots

    # ------------------------------------------------------------------- seed

    def _seed(self, *, options, bots, tester_id, tester_username, start_bonuses) -> dict:
        keep_existing = options["keep_existing"]
        report: dict = {"venues": [], "admin_created": False}
        report["admin_created"] = self._ensure_platform_admin(
            username=options["admin_username"].strip(),
            email=options["admin_email"].strip(),
            password=options["admin_password"],
            keep_existing=keep_existing,
        )

        tester = None
        if tester_id is not None:
            tester, _ = TelegramAccount.objects.get_or_create(telegram_id=tester_id)
            if tester_username and tester.username != tester_username:
                tester.username = tester_username
                tester.save(update_fields=["username", "updated_at"])

        for venue in VENUES:
            existed = Partner.objects.filter(slug=venue.slug).exists()
            partner = self._ensure_partner(venue, keep_existing=keep_existing and existed)
            if not (keep_existing and existed):
                self._seed_catalogue(partner, options["staff_password"])
            bot = self._ensure_bot(partner, venue, bots.get(venue.slug))
            tester_note = ""
            if tester is not None:
                tester_note = self._bind_tester(partner, tester, start_bonuses)
            report["venues"].append(
                {"partner": partner, "bot": bot, "created": not existed, "tester": tester_note}
            )
        return report

    @staticmethod
    def _ensure_platform_admin(*, username: str, email: str, password: str, keep_existing: bool):
        user_model = get_user_model()
        user = user_model.objects.filter(username=username).first()
        if user is not None and user.partner_id is not None:
            raise CommandError(
                f"Пользователь `{username}` принадлежит заведению; выберите другое имя "
                "администратора платформы."
            )
        if user is not None and keep_existing:
            return False
        created = user is None
        if user is None:
            user = user_model(username=username)
        user.email = email
        user.first_name = "Администратор"
        user.last_name = "платформы"
        user.role = user_model.Role.ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()
        return created

    @staticmethod
    def _ensure_partner(venue: VenueSeed, *, keep_existing: bool) -> Partner:
        partner, _ = Partner.objects.get_or_create(
            slug=venue.slug,
            defaults={
                "name": venue.name,
                "timezone": "Europe/Kyiv",
                "contact_phone": venue.phone,
                "status": Partner.Status.ACTIVE,
            },
        )
        if keep_existing:
            return partner
        partner.name = venue.name
        partner.timezone = "Europe/Kyiv"
        partner.contact_phone = venue.phone
        partner.status = Partner.Status.ACTIVE
        partner.save()

        is_hookah = venue.slug == HOOKAH_SLUG
        settings, _ = PartnerBotSettings.objects.get_or_create(partner=partner)
        settings.allow_menu_without_session = True
        settings.module_menu_enabled = True
        settings.module_tables_enabled = True
        settings.module_cart_enabled = True
        settings.module_orders_enabled = True
        settings.module_billing_enabled = True
        settings.module_staff_call_enabled = True
        settings.module_quick_sale_enabled = True
        settings.module_reports_enabled = True
        # Riverstone delivers (60 грн, free from 800, minimum 200); Night Owl
        # does not, so the two venues show both setups.
        settings.module_delivery_enabled = not is_hookah
        settings.delivery_fee = Decimal("60.00")
        settings.delivery_free_from = Decimal("800.00")
        settings.delivery_min_order = Decimal("200.00")
        settings.module_pickup_enabled = True
        # Payment options (PQ-033): Riverstone offers both, so the guest is
        # asked how to pay; Night Owl takes only transfers in advance.
        settings.pickup_pay_transfer = True
        settings.pickup_pay_on_receipt = not is_hookah
        settings.delivery_pay_transfer = True
        settings.delivery_pay_on_receipt = not is_hookah
        # Shipping by post (PQ-034): Riverstone sends coffee beans and tea by
        # Нова Пошта or Укрпошта; the guest picks the carrier, the postage
        # (carrier's tariff or 70 грн in the order, free from 1000) and the
        # payment (transfer or cash on delivery). Night Owl does not ship.
        settings.module_shipping_enabled = not is_hookah
        settings.shipping_nova_poshta = True
        settings.shipping_ukrposhta = True
        settings.shipping_meest = False
        settings.shipping_cost_carrier = True
        settings.shipping_cost_fixed = True
        settings.shipping_fee = Decimal("70.00")
        settings.shipping_free_from = Decimal("1000.00")
        settings.shipping_min_order = Decimal("0.00")
        settings.shipping_pay_transfer = True
        settings.shipping_pay_on_receipt = True
        settings.transfer_payment_details = TRANSFER_DETAILS
        # Languages (PQ-040): Ukrainian first; the guest's Telegram language
        # (Russian, English) is offered too.
        settings.default_language = "uk"
        settings.language_uk = settings.language_ru = settings.language_en = True
        settings.staff_call_waiter_enabled = True
        settings.staff_call_bartender_enabled = True
        settings.staff_call_hookah_enabled = is_hookah
        settings.full_clean()
        settings.save()

        # Night Owl works 18:00–04:00 (pickup is refused in the daytime: handy to
        # see the "closed" message). Riverstone has no common hours (pickup is
        # always open) but delivers only 10:00–22:00: hours per module.
        OpeningHours.objects.filter(partner=partner).delete()
        if is_hookah:
            OpeningHours.objects.bulk_create(
                OpeningHours(partner=partner, weekday=day, opens_at=time(18), closes_at=time(4))
                for day in range(7)
            )
        else:
            OpeningHours.objects.bulk_create(
                OpeningHours(
                    partner=partner,
                    scope=OpeningHours.Scope.DELIVERY,
                    weekday=day,
                    opens_at=time(10),
                    closes_at=time(22),
                )
                for day in range(7)
            )
        return partner

    def _seed_catalogue(self, partner: Partner, staff_password: str) -> None:
        if partner.slug == HOOKAH_SLUG:
            self._seed_staff(partner, self._smoke_staff(), staff_password)
            self._seed_tables(partner, self._smoke_tables())
            self._seed_smoke_menu(partner)
            self._seed_bonus_programs(partner, include_milestone=False)
        else:
            self._seed_staff(partner, self._cafe_staff(), staff_password)
            self._seed_tables(partner, self._cafe_tables())
            self._seed_cafe_menu(partner)
            self._seed_bonus_programs(partner, include_milestone=True)
        self._seed_traffic_sources(partner)

    @staticmethod
    def _seed_traffic_sources(partner: Partner) -> None:
        for code, name in (("instagram", "Instagram"), ("flyer", "Листовка у входа")):
            TrafficSource.objects.update_or_create(
                partner=partner, code=code, defaults={"name": name, "is_active": True}
            )

    @staticmethod
    def _ensure_bot(partner: Partner, venue: VenueSeed, token_and_username) -> BotInstance:
        bot = partner.bot_instances.order_by("created_at").first()
        if bot is None:
            bot = BotInstance(
                partner=partner,
                username=venue.bot_username,
                display_name=venue.bot_display_name,
                mode=BotInstance.Mode.POLLING,
                is_active=False,
            )
            bot.set_token(f"{BotInstance.PLACEHOLDER_TOKEN_PREFIX}{venue.slug}")
        if token_and_username is not None:
            token, username = token_and_username
            taken = BotInstance.objects.filter(username=username).exclude(pk=bot.pk).first()
            if taken is not None:
                raise CommandError(
                    f"Бот @{username} уже подключён к заведению `{taken.partner.slug}`."
                )
            bot.username = username
            bot.mode = BotInstance.Mode.POLLING
            bot.is_active = True
            bot.set_token(token)
        elif not bot.has_usable_token:
            # A bot without a real token stays off so `runbot` can start the others.
            bot.is_active = False
            bot.set_token(f"{BotInstance.PLACEHOLDER_TOKEN_PREFIX}{venue.slug}")
        bot.save()
        return bot

    def _bind_tester(
        self, partner: Partner, tester: TelegramAccount, start_bonuses: Decimal
    ) -> str:
        notes = []
        bound_elsewhere = (
            EmployeeProfile.objects.filter(partner=partner, telegram_account=tester)
            .select_related("user")
            .first()
        )
        owner = (
            EmployeeProfile.objects.filter(
                partner=partner, user__username=TESTER_OWNER_USERNAME[partner.slug]
            )
            .select_related("user")
            .first()
        )
        if bound_elsewhere is not None and (owner is None or bound_elsewhere.pk != owner.pk):
            notes.append(f"ваш Telegram уже у сотрудника {bound_elsewhere.user.username}")
        elif owner is not None:
            owner.telegram_account = tester
            owner.bot_notifications_enabled = True
            owner.notify_on_order_created = True
            owner.notify_on_order_status_changed = True
            owner.notify_on_guest_calls = True
            owner.notify_on_billing_requests = True
            owner.is_active = True
            owner.save()
            notes.append(f"вы — владелец {owner.user.username}")

        guest, _ = GuestProfile.objects.get_or_create(partner=partner, telegram_account=tester)
        if start_bonuses > 0:
            if self._grant_start_bonuses(guest, start_bonuses):
                notes.append(f"начислено {start_bonuses.normalize():f} бонусов")
            else:
                notes.append("стартовые бонусы уже начислены")
        return ", ".join(notes)

    @staticmethod
    def _grant_start_bonuses(guest: GuestProfile, amount: Decimal) -> bool:
        """One manual ledger credit per venue, guarded like every balance change."""

        guest = GuestProfile.objects.select_for_update().get(pk=guest.pk)
        if BonusTransaction.objects.filter(
            partner_id=guest.partner_id, idempotency_key=START_BONUS_KEY
        ).exists():
            return False
        assert_bonus_balance_reconciled(guest)
        BonusTransaction.objects.create(
            partner_id=guest.partner_id,
            guest=guest,
            transaction_type=BonusTransaction.TransactionType.MANUAL,
            amount=amount,
            comment="Стартовые бонусы для проверки (демо-данные)",
            source_type="demo_seed",
            idempotency_key=START_BONUS_KEY,
        )
        sync_bonus_balance_from_ledger(guest)
        return True

    # ----------------------------------------------------------------- report

    def _print_report(self, report: dict, options) -> None:
        out = self.stdout.write
        url = options["public_url"].rstrip("/")
        out(self.style.SUCCESS("Демо-данные Botera Shop готовы."))
        out("")
        out(f"Admin: {url}/admin/")
        if options["keep_existing"] and not report["admin_created"]:
            out(f"  администратор платформы: {options['admin_username']} (пароль не менялся)")
        else:
            out(
                f"  администратор платформы: {options['admin_username']} / "
                f"{options['admin_password']}"
            )
        out(
            "  владельцы заведений (видят только своё): smoke_owner_1, cafe_owner_1 / "
            f"{options['staff_password']} (при первом создании)"
        )
        for venue in report["venues"]:
            partner: Partner = venue["partner"]
            bot: BotInstance = venue["bot"]
            out("")
            state = "создано" if venue["created"] else "уже было"
            out(self.style.MIGRATE_HEADING(f"{partner.name} ({partner.slug}, {state})"))
            if bot.is_active and bot.has_usable_token:
                out(f"  бот: @{bot.username} — подключён, открыть: https://t.me/{bot.username}")
            else:
                env_name = next(v.env_name for v in VENUES if v.slug == partner.slug)
                out(self.style.WARNING(f"  бот: не подключён — впишите токен в {env_name}"))
            pickup = PartnerBotSettings.objects.filter(partner=partner).first()
            if pickup is not None and pickup.module_pickup_enabled:
                hours_text = _hours_text(partner, "pickup")
                payment = _payment_options(pickup, "pickup")
                out(f"  самовывоз: включён, {hours_text}, оплата: {payment}")
            if pickup is not None and pickup.module_delivery_enabled:
                fee = pickup.delivery_fee
                free = pickup.delivery_free_from
                out(
                    f"  доставка: включена, {_hours_text(partner, 'delivery')}, {fee:.0f} грн"
                    + (f", бесплатно от {free:.0f} грн" if free is not None else "")
                    + f", минимальный заказ {pickup.delivery_min_order:.0f} грн"
                    + f", оплата: {_payment_options(pickup, 'delivery')}"
                )
            if pickup is not None and pickup.module_shipping_enabled:
                carriers = ", ".join(
                    title
                    for field, title in (
                        ("shipping_nova_poshta", "Нова Пошта"),
                        ("shipping_ukrposhta", "Укрпошта"),
                        ("shipping_meest", "Meest"),
                    )
                    if getattr(pickup, field)
                )
                postage = []
                if pickup.shipping_cost_carrier:
                    postage.append("по тарифу перевозчика")
                if pickup.shipping_cost_fixed:
                    free = pickup.shipping_free_from
                    postage.append(
                        f"{pickup.shipping_fee:.0f} грн в заказе"
                        + (f" (бесплатно от {free:.0f} грн)" if free is not None else "")
                    )
                payment = _payment_options(pickup, "shipping").replace(
                    "при получении", "наложенный платёж"
                )
                out(
                    f"  отправка почтой: включена, {carriers}; пересылка: "
                    + " или ".join(postage)
                    + f"; оплата: {payment}; только «Кофе в зёрнах и чай»"
                )
            if venue["tester"]:
                out(f"  ваш Telegram: {venue['tester']}")
            sources = TrafficSource.objects.filter(partner=partner, is_active=True)
            if sources:
                out("  источники переходов (счёт — в Admin → Analytics):")
                for source in sources:
                    link = (
                        source.telegram_start_url(bot.username)
                        if bot.is_active
                        else f"/start {source.deep_link_payload}"
                    )
                    out(f"    {source.name}: {link}")
            out("  столы (ссылка = содержимое QR-кода):")
            for table in Table.objects.filter(partner=partner, is_active=True).order_by("number"):
                link = (
                    table.telegram_start_url(bot.username)
                    if bot.is_active
                    else f"/start {table.deep_link_payload}"
                )
                out(f"    {table.number:>2}. {table.name}: {link}")
        if not any(venue["tester"] for venue in report["venues"]):
            out("")
            out(
                self.style.WARNING(
                    "Чтобы проверять работу персонала (/staff) и получать уведомления, "
                    "впишите свой Telegram ID в DEMO_TELEGRAM_ID (узнать: @userinfobot)."
                )
            )

    # -------------------------------------------------------------- catalogue

    @staticmethod
    def _seed_staff(partner: Partner, staff_rows: list[StaffSeed], password: str) -> None:
        user_model = get_user_model()
        for staff in staff_rows:
            user, _ = user_model.objects.get_or_create(
                username=staff.username,
                defaults={
                    "email": staff.email,
                    "first_name": staff.first_name,
                    "last_name": staff.last_name,
                    "partner": partner,
                    "role": staff.role,
                    "is_staff": False,
                    "is_active": True,
                },
            )
            user.email = staff.email
            user.first_name = staff.first_name
            user.last_name = staff.last_name
            user.partner = partner
            user.role = staff.role
            user.is_staff = False
            user.is_active = True
            user.set_password(password)
            user.save()

            telegram_account, _ = TelegramAccount.objects.get_or_create(
                telegram_id=staff.telegram_id,
                defaults={
                    "username": staff.telegram_username,
                    "first_name": staff.first_name,
                    "last_name": staff.last_name,
                    "language_code": "ru",
                },
            )
            telegram_account.username = staff.telegram_username
            telegram_account.first_name = staff.first_name
            telegram_account.last_name = staff.last_name
            telegram_account.language_code = "ru"
            telegram_account.save()

            EmployeeProfile.objects.update_or_create(
                partner=partner,
                user=user,
                defaults={
                    "telegram_account": telegram_account,
                    "title": staff.title,
                    "hourly_rate": staff.hourly_rate,
                    "commission_rate": staff.commission_rate,
                    "bot_notifications_enabled": staff.role
                    in {"owner", "manager", "cashier", "waiter", "hookah_master"},
                    "notify_on_order_created": staff.role in {"owner", "manager", "cashier"},
                    "notify_on_order_status_changed": staff.role in {"owner", "manager", "cashier"},
                    "notify_on_guest_calls": staff.role
                    in {"owner", "manager", "cashier", "waiter", "hookah_master"},
                    "notify_on_billing_requests": staff.role
                    in {"owner", "manager", "cashier", "waiter"},
                    "is_active": True,
                },
            )
            AdminAccessProfile.objects.update_or_create(
                user=user,
                defaults={
                    "partner": partner,
                    "preset": (
                        AdminAccessPreset.OWNER
                        if staff.role == "owner"
                        else AdminAccessPreset.CUSTOM
                    ),
                    "is_partner_owner": staff.role == "owner",
                    "can_access_admin": staff.role == "owner",
                    "is_active": True,
                },
            )

    @staticmethod
    def _seed_tables(partner: Partner, table_rows: list[TableSeed]) -> None:
        for table in table_rows:
            Table.objects.update_or_create(
                partner=partner,
                number=table.number,
                defaults={
                    "name": table.name,
                    "seats": table.seats,
                    "qr_token": table.qr_token,
                    "is_active": True,
                },
            )

    @staticmethod
    def _ensure_category(partner: Partner, name: str, sort_order: int) -> MenuCategory:
        category, _ = MenuCategory.objects.get_or_create(
            partner=partner,
            name=name,
            defaults={"sort_order": sort_order, "is_active": True},
        )
        category.sort_order = sort_order
        category.is_active = True
        category.save()
        return category

    @staticmethod
    def _seed_category_items(
        partner: Partner,
        category: MenuCategory,
        *,
        items: list[MenuItemSeed],
        pickup: bool = True,
        shipping: bool = False,
    ) -> None:
        for item in items:
            MenuItem.objects.update_or_create(
                partner=partner,
                public_id=item.public_id,
                defaults={
                    "category": category,
                    "name": item.name,
                    "description": item.description,
                    "price": item.price,
                    "sort_order": item.sort_order,
                    "item_type": MenuItem.ItemType.INTERNAL,
                    "is_available": True,
                    "available_at_table": True,
                    "available_for_pickup": pickup,
                    "available_for_delivery": pickup,
                    "available_for_shipping": shipping,
                },
            )

    def _seed_smoke_menu(self, partner: Partner) -> None:
        hookahs = self._ensure_category(partner, "Кальяны", 10)
        signatures = self._ensure_category(partner, "Фирменные миксы", 20)
        soft_drinks = self._ensure_category(partner, "Безалкогольные напитки", 30)
        alcohol = self._ensure_category(partner, "Алкоголь", 40)

        self._seed_category_items(
            partner,
            hookahs,
            pickup=False,
            items=[
                MenuItemSeed("SMKHK001", "Classic Hookah", Decimal("450.00"), 10),
                MenuItemSeed("SMKHK002", "Premium Hookah", Decimal("650.00"), 20),
                MenuItemSeed("SMKHK003", "Fruit Bowl Hookah", Decimal("820.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            signatures,
            pickup=False,
            items=[
                MenuItemSeed(
                    "SMKSG001",
                    "Night Owl Mix",
                    Decimal("720.00"),
                    10,
                    "Dark leaf, citrus, light spice.",
                ),
                MenuItemSeed(
                    "SMKSG002",
                    "Kyiv Sunset Mix",
                    Decimal("760.00"),
                    20,
                    "Berry profile with creamy finish.",
                ),
            ],
        )
        self._seed_category_items(
            partner,
            soft_drinks,
            items=[
                MenuItemSeed("SMKDR001", "Homemade Lemonade", Decimal("150.00"), 10),
                MenuItemSeed("SMKDR002", "Espresso", Decimal("95.00"), 20),
                MenuItemSeed("SMKDR003", "Craft Tea Pot", Decimal("180.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            alcohol,
            pickup=False,
            items=[
                MenuItemSeed("SMKAL001", "Aperol Spritz", Decimal("240.00"), 10),
                MenuItemSeed("SMKAL002", "Negroni", Decimal("250.00"), 20),
                MenuItemSeed("SMKAL003", "Whiskey Sour", Decimal("260.00"), 30),
                MenuItemSeed("SMKAL004", "House Wine Glass", Decimal("190.00"), 40),
            ],
        )

    def _seed_cafe_menu(self, partner: Partner) -> None:
        breakfast = self._ensure_category(partner, "Завтраки", 10)
        salads = self._ensure_category(partner, "Салаты", 20)
        mains = self._ensure_category(partner, "Основные блюда", 30)
        desserts = self._ensure_category(partner, "Десерты", 40)
        soft_drinks = self._ensure_category(partner, "Кофе и чай", 50)
        alcohol = self._ensure_category(partner, "Алкоголь", 60)
        # Goods that travel by post (PQ-034); the rest of the menu does not.
        beans = self._ensure_category(partner, "Кофе в зёрнах и чай", 70)

        self._seed_category_items(
            partner,
            breakfast,
            items=[
                MenuItemSeed("CAFBR001", "Syrniki", Decimal("220.00"), 10),
                MenuItemSeed("CAFBR002", "Eggs Benedict", Decimal("295.00"), 20),
                MenuItemSeed("CAFBR003", "Granola Bowl", Decimal("180.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            salads,
            items=[
                MenuItemSeed("CAFSL001", "Caesar with Chicken", Decimal("260.00"), 10),
                MenuItemSeed("CAFSL002", "Greek Salad", Decimal("210.00"), 20),
            ],
        )
        self._seed_category_items(
            partner,
            mains,
            items=[
                MenuItemSeed("CAFMN001", "Chicken Steak", Decimal("340.00"), 10),
                MenuItemSeed("CAFMN002", "Pasta Carbonara", Decimal("285.00"), 20),
                MenuItemSeed("CAFMN003", "Salmon with Vegetables", Decimal("420.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            desserts,
            items=[
                MenuItemSeed("CAFDS001", "Cheesecake", Decimal("170.00"), 10),
                MenuItemSeed("CAFDS002", "Napoleon", Decimal("165.00"), 20),
            ],
        )
        self._seed_category_items(
            partner,
            soft_drinks,
            items=[
                MenuItemSeed("CAFDR001", "Cappuccino", Decimal("110.00"), 10),
                MenuItemSeed("CAFDR002", "Flat White", Decimal("125.00"), 20),
                MenuItemSeed("CAFDR003", "Tea Pot", Decimal("140.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            alcohol,
            pickup=False,
            items=[
                MenuItemSeed("CAFAL001", "Mimosa", Decimal("210.00"), 10),
                MenuItemSeed("CAFAL002", "House White Wine", Decimal("195.00"), 20),
                MenuItemSeed("CAFAL003", "Craft Beer", Decimal("165.00"), 30),
            ],
        )
        self._seed_category_items(
            partner,
            beans,
            shipping=True,
            items=[
                MenuItemSeed("CAFBN001", "Кофе в зёрнах Эфиопия, 250 г", Decimal("320.00"), 10),
                MenuItemSeed("CAFBN002", "Кофе в зёрнах Бразилия, 250 г", Decimal("290.00"), 20),
                MenuItemSeed("CAFBN003", "Чай листовой, 100 г", Decimal("180.00"), 30),
            ],
        )

    @staticmethod
    def _seed_bonus_programs(partner: Partner, *, include_milestone: bool) -> None:
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Visit reward",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.VISIT,
                "program_type": BonusProgram.ProgramType.VISIT,
                "strategy_code": "",
                "fixed_amount": Decimal("20.00"),
                "percent": Decimal("0.00"),
                "min_order_total": Decimal("0.00"),
                "milestone_order_count": 0,
                "max_redeem_share": Decimal("30.00"),
                "expires_in_days": 30,
                "config": {"once_per_day": True},
                "is_active": True,
            },
        )
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Cashback 5%",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.ORDER_COMPLETED,
                "program_type": BonusProgram.ProgramType.CASHBACK,
                "strategy_code": "",
                "fixed_amount": Decimal("0.00"),
                "percent": Decimal("5.00"),
                "min_order_total": Decimal("100.00"),
                "milestone_order_count": 0,
                "max_redeem_share": Decimal("30.00"),
                "expires_in_days": 90,
                "config": {},
                "is_active": True,
            },
        )
        BonusProgram.objects.update_or_create(
            partner=partner,
            name="Walk-in cashback 5%",
            defaults={
                "trigger_event": BonusProgram.TriggerEvent.MANUAL_PURCHASE,
                "program_type": BonusProgram.ProgramType.CASHBACK,
                "strategy_code": "",
                "fixed_amount": Decimal("0.00"),
                "percent": Decimal("5.00"),
                "min_order_total": Decimal("100.00"),
                "milestone_order_count": 0,
                "max_redeem_share": Decimal("30.00"),
                "expires_in_days": 90,
                "config": {},
                "is_active": True,
            },
        )
        if include_milestone:
            BonusProgram.objects.update_or_create(
                partner=partner,
                name="Every 5th order bonus",
                defaults={
                    "trigger_event": BonusProgram.TriggerEvent.ORDER_COMPLETED,
                    "program_type": BonusProgram.ProgramType.MILESTONE,
                    "strategy_code": "",
                    "fixed_amount": Decimal("75.00"),
                    "percent": Decimal("0.00"),
                    "min_order_total": Decimal("0.00"),
                    "milestone_order_count": 5,
                    "max_redeem_share": Decimal("30.00"),
                    "expires_in_days": 60,
                    "config": {},
                    "is_active": True,
                },
            )

    @staticmethod
    def _smoke_staff() -> list[StaffSeed]:
        return [
            StaffSeed(
                "smoke_owner_1",
                "owner1@nightowl.local",
                "Ivan",
                "Moroz",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700100001,
                "smoke_owner_1",
            ),
            StaffSeed(
                "smoke_owner_2",
                "owner2@nightowl.local",
                "Oleh",
                "Kravets",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700100002,
                "smoke_owner_2",
            ),
            StaffSeed(
                "smoke_owner_3",
                "owner3@nightowl.local",
                "Anna",
                "Shevchenko",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700100003,
                "smoke_owner_3",
            ),
            StaffSeed(
                "smoke_manager_1",
                "manager@nightowl.local",
                "Maksym",
                "Bondar",
                "manager",
                "Управляющий",
                Decimal("220.00"),
                Decimal("2.50"),
                700100004,
                "smoke_manager_1",
            ),
            StaffSeed(
                "smoke_hookah_1",
                "hookah@nightowl.local",
                "Denys",
                "Tkachenko",
                "hookah_master",
                "Кальянный мастер",
                Decimal("180.00"),
                Decimal("5.00"),
                700100005,
                "smoke_hookah_1",
            ),
        ]

    @staticmethod
    def _cafe_staff() -> list[StaffSeed]:
        return [
            StaffSeed(
                "cafe_owner_1",
                "owner1@riverstone.local",
                "Svitlana",
                "Petrenko",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700200001,
                "cafe_owner_1",
            ),
            StaffSeed(
                "cafe_owner_2",
                "owner2@riverstone.local",
                "Taras",
                "Melnyk",
                "owner",
                "Co-owner",
                Decimal("0.00"),
                Decimal("0.00"),
                700200002,
                "cafe_owner_2",
            ),
            StaffSeed(
                "cafe_manager_1",
                "manager@riverstone.local",
                "Yuliia",
                "Hnatiuk",
                "manager",
                "Hall Manager",
                Decimal("230.00"),
                Decimal("2.00"),
                700200003,
                "cafe_manager_1",
            ),
            StaffSeed(
                "cafe_barman_1",
                "barman@riverstone.local",
                "Roman",
                "Lysenko",
                "cashier",
                "Бармен",
                Decimal("170.00"),
                Decimal("3.00"),
                700200004,
                "cafe_barman_1",
            ),
            StaffSeed(
                "cafe_waiter_1",
                "waiter1@riverstone.local",
                "Iryna",
                "Koval",
                "waiter",
                "Официант",
                Decimal("145.00"),
                Decimal("4.00"),
                700200005,
                "cafe_waiter_1",
            ),
            StaffSeed(
                "cafe_waiter_2",
                "waiter2@riverstone.local",
                "Nazar",
                "Kushnir",
                "waiter",
                "Официант",
                Decimal("145.00"),
                Decimal("4.00"),
                700200006,
                "cafe_waiter_2",
            ),
            StaffSeed(
                "cafe_waiter_3",
                "waiter3@riverstone.local",
                "Olha",
                "Romanenko",
                "waiter",
                "Официант",
                Decimal("145.00"),
                Decimal("4.00"),
                700200007,
                "cafe_waiter_3",
            ),
            StaffSeed(
                "cafe_waiter_4",
                "waiter4@riverstone.local",
                "Artem",
                "Savchuk",
                "waiter",
                "Официант",
                Decimal("145.00"),
                Decimal("4.00"),
                700200008,
                "cafe_waiter_4",
            ),
        ]

    @staticmethod
    def _smoke_tables() -> list[TableSeed]:
        return [
            TableSeed(1, "Table 1", 4, "nightowl_tbl_01"),
            TableSeed(2, "Table 2", 4, "nightowl_tbl_02"),
            TableSeed(3, "Table 3", 4, "nightowl_tbl_03"),
            TableSeed(4, "Table 4", 4, "nightowl_tbl_04"),
            TableSeed(5, "Table 5", 6, "nightowl_tbl_05"),
            TableSeed(6, "Table 6", 6, "nightowl_tbl_06"),
            TableSeed(7, "Table 7", 8, "nightowl_tbl_07"),
            TableSeed(8, "Bar", 6, "nightowl_bar_08"),
        ]

    @staticmethod
    def _cafe_tables() -> list[TableSeed]:
        return [
            TableSeed(1, "Table 1", 2, "river_tbl_01"),
            TableSeed(2, "Table 2", 2, "river_tbl_02"),
            TableSeed(3, "Table 3", 2, "river_tbl_03"),
            TableSeed(4, "Table 4", 4, "river_tbl_04"),
            TableSeed(5, "Table 5", 4, "river_tbl_05"),
            TableSeed(6, "Table 6", 4, "river_tbl_06"),
            TableSeed(7, "Table 7", 4, "river_tbl_07"),
            TableSeed(8, "Table 8", 4, "river_tbl_08"),
            TableSeed(9, "Table 9", 6, "river_tbl_09"),
            TableSeed(10, "Table 10", 6, "river_tbl_10"),
            TableSeed(11, "Table 11", 6, "river_tbl_11"),
            TableSeed(12, "Table 12", 8, "river_tbl_12"),
        ]
