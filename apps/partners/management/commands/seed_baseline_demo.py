"""Demo data for local testing: four venues set up differently.

What the venues are — ``apps/partners/management/demo_data.py``: Night Owl
(two points in one network, one of them with everything on), Riverstone Cafe
and the house-plant shop «Зелена полиця».

Every option also reads an environment variable (see ``.env.example``), so the
Docker ``init`` service seeds from ``.env`` with no arguments:

- ``DEMO_HOOKAH_BOT_TOKEN`` / ``DEMO_CAFE_BOT_TOKEN`` / ``DEMO_SHOP_BOT_TOKEN``
  — BotFather tokens; the bot username is looked up with ``getMe`` unless
  ``DEMO_*_BOT_USERNAME`` is set. Without a token the venue's bot stays
  inactive. The Night Owl bot serves both points of the network.
- ``DEMO_TELEGRAM_ID`` — your own Telegram user id: it becomes an owner of
  every venue (the owner of the Night Owl network works in both points) and
  gets start bonuses as a guest.

``--keep-existing`` (used by Docker on every start) creates a venue only when
it is missing and otherwise only applies bot tokens and your Telegram binding,
so changes made in Admin survive a restart. Without it the demo venues are
reset to these defaults (photos uploaded in Admin are kept).
``docker compose down -v`` wipes everything.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from apps.analytics.models import TrafficSource
from apps.bonuses.models import BonusProgram, BonusTransaction
from apps.bonuses.services import assert_bonus_balance_reconciled, sync_bonus_balance_from_ledger
from apps.employees.models import EmployeeProfile
from apps.employees.network_staff import sync_network_staff
from apps.menu.models import (
    MenuBadge,
    MenuCategory,
    MenuItem,
    MenuItemAttribute,
    MenuItemVariant,
    Modifier,
    ModifierGroup,
)
from apps.orders.kitchen import issue_screen_link
from apps.orders.models import KitchenScreen
from apps.partners.management import demo_data as demo
from apps.partners.models import BotInstance, Network, OpeningHours, Partner, PartnerBotSettings
from apps.tables.models import Table
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, GuestProfile, TelegramAccount
from core.security.environment import is_production

# Kept for imports elsewhere: the first two venues' slugs.
HOOKAH_SLUG = demo.PODIL
CAFE_SLUG = demo.CAFE
START_BONUS_KEY = "demo-seed:start-bonus"


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


def _portion(text: str):
    if not text:
        return None, ""
    amount, unit = text.split()
    return Decimal(amount), unit


class Command(BaseCommand):
    help = (
        "Демо-данные для локальной проверки: четыре заведения с разными настройками — "
        "сеть Night Owl (две точки, одна со всеми модулями), кафе Riverstone и магазин "
        "растений «Зелена полиця»; токены ботов и ваш Telegram ID — из .env."
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
        for venue in demo.VENUES:
            if venue.bot is None:
                continue
            key, env_name = venue.bot[0], venue.bot[1]
            parser.add_argument(f"--{key}-bot-token", default=_env(env_name))
            parser.add_argument(
                f"--{key}-bot-username", default=_env(env_name.replace("_TOKEN", "_USERNAME"))
            )
        parser.add_argument(
            "--telegram-id",
            default=_env("DEMO_TELEGRAM_ID"),
            help="Ваш Telegram ID: станет владельцем заведений и получит бонусы.",
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
        for venue in demo.VENUES:
            if venue.bot is None:
                continue
            key, env_name = venue.bot[0], venue.bot[1]
            token = (options[f"{key}_bot_token"] or "").strip()
            if not token:
                continue
            if not BotInstance.looks_like_valid_token(token):
                raise CommandError(
                    f"{env_name}: это не похоже на токен бота (вид 123456:ABC...)."
                )
            username = BotInstance.normalize_username(options[f"{key}_bot_username"] or "")
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
        report: dict = {"venues": [], "admin_created": False, "network": None}
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

        partners: dict[str, Partner] = {}
        fresh: dict[str, bool] = {}
        for venue in demo.VENUES:
            existed = Partner.objects.filter(slug=venue.slug).exists()
            fresh[venue.slug] = not (keep_existing and existed)
            partner = self._ensure_partner(venue, reset=fresh[venue.slug])
            partners[venue.slug] = partner
            screens = []
            if fresh[venue.slug]:
                self._seed_catalogue(partner, options["staff_password"])
                screens = self._seed_screens(partner)
            bot = (
                self._ensure_bot(partner, venue, bots.get(venue.slug))
                if venue.bot is not None else None
            )
            report["venues"].append({
                "venue": venue, "partner": partner, "bot": bot, "created": not existed,
                "screens": screens, "tester": "",
            })

        # The Night Owl network: when one of its points is created or reset.
        network = Network.objects.filter(points__slug=demo.PODIL).first()
        if any(fresh[slug] for slug in demo.NETWORK_POINTS):
            network = self._ensure_network(partners)
        for venue in demo.VENUES:
            if fresh[venue.slug]:
                self._seed_staff_points(partners, venue.slug)
                self._seed_traffic_sources(partners[venue.slug])

        if tester is not None:
            for entry in report["venues"]:
                venue = entry["venue"]
                if venue.tester_owner:
                    entry["tester"] = self._bind_tester(
                        entry["partner"], tester, start_bonuses, venue.tester_owner
                    )
        if network is not None:
            sync_network_staff(network.id)
            report["network"] = network
            if tester is not None:
                for entry in report["venues"]:
                    if not entry["venue"].tester_owner and EmployeeProfile.objects.filter(
                        partner=entry["partner"], telegram_account=tester,
                        mirrored_from__isnull=False, is_active=True,
                    ).exists():
                        entry["tester"] = "вы — владелец сети (ваш профиль из Поділ)"
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
    def _ensure_partner(venue: demo.VenueSeed, *, reset: bool) -> Partner:
        partner, _ = Partner.objects.get_or_create(
            slug=venue.slug,
            defaults={
                "name": venue.name,
                "timezone": "Europe/Kyiv",
                "contact_phone": venue.phone,
                "address": venue.address,
                "status": Partner.Status.ACTIVE,
            },
        )
        if not reset:
            return partner
        partner.name = venue.name
        partner.timezone = "Europe/Kyiv"
        partner.contact_phone = venue.phone
        partner.address = venue.address
        partner.status = Partner.Status.ACTIVE
        partner.save()

        settings, _ = PartnerBotSettings.objects.get_or_create(partner=partner)
        # Everything off first: each venue switches on its own set.
        for field in PartnerBotSettings._meta.concrete_fields:
            if field.name.startswith("module_") or field.name.startswith("staff_call_"):
                setattr(settings, field.name, False)
        for name, value in {**demo.COMMON_SETTINGS, **demo.SETTINGS[venue.slug]}.items():
            setattr(settings, name, value)
        settings.full_clean()
        settings.save()

        OpeningHours.objects.filter(partner=partner).delete()
        OpeningHours.objects.bulk_create(
            OpeningHours(partner=partner, scope=scope, weekday=day, opens_at=opens,
                         closes_at=closes)
            for scope, opens, closes in demo.HOURS[venue.slug]
            for day in range(7)
        )
        return partner

    def _seed_catalogue(self, partner: Partner, staff_password: str) -> None:
        self._seed_staff(partner, demo.STAFF[partner.slug], staff_password)
        self._retire_staff(partner)
        self._seed_tables(partner, demo.TABLES[partner.slug])
        self._seed_menu(partner, demo.MENUS[partner.slug])
        self._seed_bonus_programs(partner, demo.BONUSES[partner.slug])

    @staticmethod
    def _seed_traffic_sources(partner: Partner) -> None:
        for code, name, whole_network in demo.SOURCES[partner.slug]:
            TrafficSource.objects.update_or_create(
                partner=partner, code=code,
                defaults={"name": name, "is_active": True, "whole_network": whole_network},
            )

    @staticmethod
    def _seed_screens(partner: Partner) -> list[tuple[KitchenScreen, str]]:
        """Kitchen and bar screens; a new screen gets its link (printed once)."""

        found = []
        for name, shows in demo.SCREENS[partner.slug]:
            screen, _ = KitchenScreen.objects.get_or_create(
                partner=partner, name=name, defaults={"shows": shows}
            )
            screen.shows = shows
            screen.is_active = True
            screen.save()
            token = "" if screen.token_hash else issue_screen_link(screen)
            found.append((screen, token))
        return found

    @staticmethod
    def _ensure_network(partners: dict[str, Partner]) -> Network:
        podil, obolon = partners[demo.PODIL], partners[demo.OBOLON]
        network = podil.network or obolon.network or Network(name=demo.NETWORK_NAME)
        network.name = demo.NETWORK_NAME
        network.bonus_mode = Network.BonusMode.SHARED
        network.save()
        for point in (podil, obolon):
            if point.network_id != network.id:
                point.network = network
                point.save(update_fields=["network", "updated_at"])
        # One bot for both points: Поділ's (Оболонь has none of its own).
        network.bot = podil.bot_instances.order_by("created_at").first()
        network.save()
        return network

    @staticmethod
    def _seed_staff_points(partners: dict[str, Partner], slug: str) -> None:
        """A manager's other points of the network (PQ-047)."""

        for staff in demo.STAFF[slug]:
            access = AdminAccessProfile.objects.filter(user__username=staff.username).first()
            if access is not None:
                access.extra_partners.set(
                    [partners[point] for point in staff.extra_points if point in partners]
                )

    @staticmethod
    def _ensure_bot(partner: Partner, venue: demo.VenueSeed, token_and_username) -> BotInstance:
        _key, _env_name, default_username, display_name = venue.bot
        bot = partner.bot_instances.order_by("created_at").first()
        if bot is None:
            bot = BotInstance(
                partner=partner,
                username=default_username,
                display_name=display_name,
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
        self, partner: Partner, tester: TelegramAccount, start_bonuses: Decimal, owner_name: str
    ) -> str:
        notes = []
        bound_elsewhere = (
            EmployeeProfile.objects.filter(
                partner=partner, telegram_account=tester, mirrored_from__isnull=True
            )
            .select_related("user")
            .first()
        )
        owner = (
            EmployeeProfile.objects.filter(partner=partner, user__username=owner_name)
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
            "  владельцы (видят только своё): smoke_owner_1 (сеть Night Owl — обе точки), "
            f"cafe_owner_1, plant_owner_1 / {options['staff_password']} (при первом создании)"
        )
        out(
            "  менеджеры с Admin: smoke_manager_1 (Поділ и Оболонь), obolon_manager_1, "
            "cafe_manager_1, plant_manager_1"
        )
        network = report["network"]
        network_bot = network.bot if network is not None else None
        webapp_base = (django_settings.WEBAPP_BASE_URL or "").rstrip("/")
        for entry in report["venues"]:
            self._print_venue(entry, url=url, webapp_base=webapp_base, network_bot=network_bot)
        if network is not None:
            out("")
            out(self.style.MIGRATE_HEADING(f"Сеть «{network.name}»"))
            points = ", ".join(network.points.order_by("name").values_list("name", flat=True))
            shared = network.bonus_mode == Network.BonusMode.SHARED
            bonuses = "общие: один баланс в обеих точках" if shared else "раздельные"
            out(f"  точки: {points}; бонусы — {bonuses}")
            if network_bot is not None and network_bot.is_active:
                out(f"  бот сети: @{network_bot.username} — гость сначала выбирает точку")
            out(
                "  владельцы smoke_owner_1, smoke_owner_2 и менеджер smoke_manager_1 работают "
                "в обеих точках; отчёт по сети — Admin → «Сети заведений»"
            )
        if not any(entry["tester"] for entry in report["venues"]):
            out("")
            out(
                self.style.WARNING(
                    "Чтобы проверять работу персонала (/staff) и получать уведомления, "
                    "впишите свой Telegram ID в DEMO_TELEGRAM_ID (узнать: @userinfobot)."
                )
            )

    def _print_venue(self, entry: dict, *, url: str, webapp_base: str, network_bot) -> None:
        out = self.stdout.write
        partner: Partner = entry["partner"]
        venue: demo.VenueSeed = entry["venue"]
        bot: BotInstance | None = entry["bot"]
        settings = PartnerBotSettings.objects.filter(partner=partner).first()
        out("")
        state = "создано" if entry["created"] else "уже было"
        out(self.style.MIGRATE_HEADING(f"{partner.name} ({partner.slug}, {state})"))
        if settings is not None:
            modules = [title for field, title in demo.MODULE_NAMES if getattr(settings, field)]
            out("  модули: " + ", ".join(modules))
        link_bot = bot if bot is not None else network_bot
        if bot is None:
            name = f"@{network_bot.username}" if network_bot is not None else "—"
            out(f"  бот: своего нет — работает бот сети {name}")
        elif bot.is_active and bot.has_usable_token:
            out(f"  бот: @{bot.username} — подключён, открыть: https://t.me/{bot.username}")
        else:
            out(self.style.WARNING(f"  бот: не подключён — впишите токен в {venue.bot[1]}"))
        if settings is not None:
            self._print_channels(partner, settings)
            if settings.module_webapp_enabled:
                out(
                    "  Mini App: кнопка в боте"
                    if webapp_base
                    else "  Mini App: включён — для кнопки в боте нужен WEBAPP_BASE_URL (HTTPS)"
                )
        for screen, token in entry["screens"]:
            base = webapp_base or url
            if token:
                out(f"  экран «{screen.name}»: {base}{reverse('webapp:kitchen')}#{token}"
                    " (ссылка показывается один раз)")
            else:
                out(f"  экран «{screen.name}»: ссылка выдана раньше; новая — в Admin")
        if entry["tester"]:
            out(f"  ваш Telegram: {entry['tester']}")
        active = link_bot is not None and link_bot.is_active
        sources = TrafficSource.objects.filter(partner=partner, is_active=True)
        if sources:
            out("  источники переходов (счёт — в Admin → Analytics):")
            for source in sources:
                link = (
                    source.telegram_start_url(link_bot.username) if active
                    else f"/start {source.deep_link_payload}"
                )
                suffix = " (вся сеть)" if source.whole_network else ""
                out(f"    {source.name}{suffix}: {link}")
        tables = Table.objects.filter(partner=partner, is_active=True).order_by("number")
        if tables:
            out("  столы (ссылка = содержимое QR-кода):")
            for table in tables:
                link = (
                    table.telegram_start_url(link_bot.username) if active
                    else f"/start {table.deep_link_payload}"
                )
                out(f"    {table.number:>2}. {table.name}: {link}")

    def _print_channels(self, partner: Partner, settings: PartnerBotSettings) -> None:
        out = self.stdout.write
        if settings.module_pickup_enabled:
            out(
                f"  самовывоз: {_hours_text(partner, 'pickup')}, "
                f"оплата: {_payment_options(settings, 'pickup')}"
            )
        if settings.module_delivery_enabled:
            free = settings.delivery_free_from
            out(
                f"  доставка: {_hours_text(partner, 'delivery')}, {settings.delivery_fee:.0f} грн"
                + (f", бесплатно от {free:.0f} грн" if free is not None else "")
                + f", минимальный заказ {settings.delivery_min_order:.0f} грн"
                + f", оплата: {_payment_options(settings, 'delivery')}"
            )
        if settings.module_shipping_enabled:
            carriers = ", ".join(
                title
                for field, title in (
                    ("shipping_nova_poshta", "Нова Пошта"),
                    ("shipping_ukrposhta", "Укрпошта"),
                    ("shipping_meest", "Meest"),
                )
                if getattr(settings, field)
            )
            postage = []
            if settings.shipping_cost_carrier:
                postage.append("по тарифу перевозчика")
            if settings.shipping_cost_fixed:
                free = settings.shipping_free_from
                postage.append(
                    f"{settings.shipping_fee:.0f} грн в заказе"
                    + (f" (бесплатно от {free:.0f} грн)" if free is not None else "")
                )
            payment = _payment_options(settings, "shipping").replace(
                "при получении", "наложенный платёж"
            )
            out(
                f"  отправка почтой: {carriers}; пересылка: " + " или ".join(postage)
                + f"; оплата: {payment}"
            )

    # -------------------------------------------------------------- catalogue

    @staticmethod
    def _seed_staff(partner: Partner, staff_rows: list[demo.StaffSeed], password: str) -> None:
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
            user.is_active = True
            user.set_password(password)
            user.save()

            telegram_account, _ = TelegramAccount.objects.get_or_create(
                telegram_id=staff.telegram_id,
                defaults={
                    "username": staff.telegram_username,
                    "first_name": staff.first_name,
                    "last_name": staff.last_name,
                    "language_code": "uk",
                },
            )
            telegram_account.username = staff.telegram_username
            telegram_account.first_name = staff.first_name
            telegram_account.last_name = staff.last_name
            telegram_account.language_code = "uk"
            telegram_account.save()

            works_orders = staff.role in {"owner", "manager", "cashier"}
            EmployeeProfile.objects.update_or_create(
                partner=partner,
                user=user,
                defaults={
                    "telegram_account": telegram_account,
                    "title": staff.title,
                    "hourly_rate": staff.hourly_rate,
                    "commission_rate": staff.commission_rate,
                    "bot_notifications_enabled": True,
                    "notify_on_order_created": works_orders,
                    "notify_on_order_status_changed": works_orders,
                    "notify_on_guest_calls": staff.role
                    in {"owner", "manager", "cashier", "waiter", "hookah_master"},
                    "notify_on_billing_requests": staff.role
                    in {"owner", "manager", "cashier", "waiter"},
                    "is_active": True,
                },
            )
            owner = staff.role == "owner"
            AdminAccessProfile.objects.update_or_create(
                user=user,
                defaults={
                    "partner": partner,
                    "preset": (
                        AdminAccessPreset.OWNER if owner
                        else AdminAccessPreset.MANAGER if staff.admin
                        else AdminAccessPreset.CUSTOM
                    ),
                    "is_partner_owner": owner,
                    "can_access_admin": owner or staff.admin,
                    "is_active": True,
                },
            )

    @staticmethod
    def _retire_staff(partner: Partner) -> None:
        for username in demo.RETIRED_STAFF.get(partner.slug, ()):
            for access in AdminAccessProfile.objects.filter(
                partner=partner, user__username=username, is_active=True
            ):
                access.is_active = False
                access.save()
            for employee in EmployeeProfile.objects.filter(
                partner=partner, user__username=username, is_active=True
            ):
                employee.is_active = False
                employee.save()

    @staticmethod
    def _seed_tables(partner: Partner, table_rows) -> None:
        for number, name, seats, qr_token in table_rows:
            Table.objects.update_or_create(
                partner=partner,
                number=number,
                defaults={"name": name, "seats": seats, "qr_token": qr_token, "is_active": True},
            )

    @staticmethod
    def _ensure_category(partner: Partner, seed: demo.CategorySeed, parent) -> MenuCategory:
        # An earlier demo used other names: rename the old category in place
        # so a re-run leaves no empty duplicates.
        if seed.old_name and not MenuCategory.objects.filter(
            partner=partner, name=seed.name
        ).exists():
            MenuCategory.objects.filter(partner=partner, name=seed.old_name).update(
                name=seed.name
            )
        category, _ = MenuCategory.objects.get_or_create(
            partner=partner, name=seed.name, defaults={"sort_order": seed.sort_order}
        )
        category.parent = parent
        category.sort_order = seed.sort_order
        category.is_active = True
        category.station = seed.station
        category.description = seed.description
        category.available_from, category.available_until = seed.schedule or (None, None)
        category.available_weekdays = list(seed.weekdays)
        category.save()
        return category

    def _seed_menu(self, partner: Partner, menu: demo.MenuSeed) -> None:
        own_badges = {}
        for order, (name, emoji) in enumerate(menu.own_badges):
            badge, _ = MenuBadge.objects.update_or_create(
                partner=partner, name=name, defaults={"emoji": emoji, "sort_order": order}
            )
            own_badges[name] = badge

        categories: dict[str, MenuCategory] = {}
        items: dict[str, MenuItem] = {}
        for seed in menu.categories:
            category = self._ensure_category(partner, seed, categories.get(seed.parent))
            categories[seed.name] = category
            for order, item_seed in enumerate(seed.items, start=1):
                items[item_seed.public_id] = self._seed_item(
                    partner, category, seed, item_seed, order * 10, own_badges
                )

        for order, group_seed in enumerate(menu.groups, start=1):
            group, _ = ModifierGroup.objects.update_or_create(
                partner=partner,
                name=group_seed.name,
                defaults={
                    "min_choices": group_seed.min_choices,
                    "max_choices": group_seed.max_choices,
                    "is_active": True,
                    "sort_order": order,
                },
            )
            names = []
            for position, (name, price) in enumerate(group_seed.options, start=1):
                Modifier.objects.update_or_create(
                    group=group,
                    name=name,
                    defaults={"price": price, "sort_order": position, "is_available": True},
                )
                names.append(name)
            group.modifiers.exclude(name__in=names).delete()
            group.categories.set([categories[name] for name in group_seed.categories])
            group.items.set([items[public_id] for public_id in group_seed.items])

    @staticmethod
    def _seed_item(partner, category, category_seed, seed: demo.ItemSeed, order, own_badges):
        portion, unit = _portion(seed.portion)
        shipping = category_seed.shipping if seed.shipping is None else seed.shipping
        item, _ = MenuItem.objects.update_or_create(
            partner=partner,
            public_id=seed.public_id,
            defaults={
                "category": category,
                "name": seed.name,
                "description": seed.description,
                "price": seed.price,
                "sort_order": order,
                "item_type": MenuItem.ItemType.INTERNAL,
                "is_available": True,
                "available_at_table": category_seed.table,
                "available_for_pickup": category_seed.pickup,
                "available_for_delivery": category_seed.pickup,
                "available_for_shipping": shipping,
                "portion": portion,
                "portion_unit": unit,
                "old_price": seed.old_price,
                "badges": list(seed.badges),
                "sku": seed.sku,
                "barcode": seed.barcode,
            },
        )
        item.custom_badges.set([own_badges[name] for name in seed.own_badges])
        item.attributes.all().delete()
        MenuItemAttribute.objects.bulk_create(
            MenuItemAttribute(partner=partner, item=item, name=name, value=value,
                              sort_order=position)
            for position, (name, value) in enumerate(seed.attributes, start=1)
        )
        names = []
        for position, (name, price) in enumerate(seed.variants, start=1):
            MenuItemVariant.objects.update_or_create(
                item=item,
                name=name,
                defaults={"price": price, "sort_order": position, "is_available": True},
            )
            names.append(name)
        item.variants.exclude(name__in=names).delete()
        return item

    @staticmethod
    def _seed_bonus_programs(partner: Partner, programs) -> None:
        # Guests see the names in «Бонусы»; rename the old English ones in place.
        for old_name, new_name in demo.OLD_BONUS_NAMES.items():
            if not BonusProgram.objects.filter(partner=partner, name=new_name).exists():
                BonusProgram.objects.filter(partner=partner, name=old_name).update(name=new_name)
        for (name, trigger, kind, fixed, percent, min_total, milestone, days,
             config) in programs:
            BonusProgram.objects.update_or_create(
                partner=partner,
                name=name,
                defaults={
                    "trigger_event": trigger,
                    "program_type": kind,
                    "strategy_code": "",
                    "fixed_amount": fixed,
                    "percent": percent,
                    "min_order_total": min_total,
                    "milestone_order_count": milestone,
                    "max_redeem_share": Decimal("30.00"),
                    "expires_in_days": days,
                    "config": dict(config),
                    "is_active": True,
                },
            )
