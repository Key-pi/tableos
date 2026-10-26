"""Create N load-test partners: bot, settings, 20 tables, 30 menu items, 4 staff.

Usage (from the repo, DJANGO_SETTINGS_MODULE set): python make_data.py N
Writes partners.json next to this script with tokens, table payloads, staff chat ids, menu ids.
"""

import json
import pathlib
import secrets
import sys
from decimal import Decimal

import django

django.setup()

from django.db import transaction  # noqa: E402

from apps.bonuses.models import BonusProgram  # noqa: E402
from apps.employees.models import EmployeeProfile  # noqa: E402
from apps.menu.models import MenuCategory, MenuItem  # noqa: E402
from apps.partners.models import BotInstance, Partner, PartnerBotSettings  # noqa: E402
from apps.tables.models import Table  # noqa: E402
from apps.users.models import TelegramAccount, User  # noqa: E402

N = int(sys.argv[1])
OUT = pathlib.Path(__file__).with_name("partners.json")
ROLES = ("owner", "manager", "cashier", "waiter")
from django.contrib.auth.hashers import make_password  # noqa: E402

PASSWORD = make_password("x")


def token(i: int) -> str:
    return f"{7000000000 + i}:AA" + secrets.token_urlsafe(32)[:33]


@transaction.atomic
def make(i: int) -> dict:
    slug = f"lt-{i:04d}"
    partner = Partner.objects.create(
        name=f"Load cafe {i}", slug=slug, timezone="Europe/Kyiv", status=Partner.Status.ACTIVE
    )
    s = PartnerBotSettings.objects.create(partner=partner)
    for field in (
        "allow_menu_without_session", "module_menu_enabled", "module_tables_enabled",
        "module_cart_enabled", "module_orders_enabled", "module_billing_enabled",
        "module_staff_call_enabled", "module_quick_sale_enabled", "module_reports_enabled",
        "module_pickup_enabled", "pickup_pay_transfer", "pickup_pay_on_receipt",
        "language_uk", "language_ru", "language_en",
    ):
        setattr(s, field, True)
    s.transfer_payment_details = "IBAN UA00 0000 0000 0000 0000 0000 000"
    s.default_language = "uk"
    s.full_clean()
    s.save()
    bot = BotInstance(
        partner=partner, username=f"lt_bot_{i}", display_name=f"LT {i}",
        mode=BotInstance.Mode.POLLING, is_active=True,
    )
    tok = token(i)
    bot.set_token(tok)
    bot.save()
    tables = [
        Table.objects.create(partner=partner, number=n, name=f"T{n}", seats=4)
        for n in range(1, 21)
    ]
    items = []
    for c in range(5):
        cat = MenuCategory.objects.create(partner=partner, name=f"Cat {c}", sort_order=c)
        for j in range(6):
            items.append(MenuItem.objects.create(
                partner=partner, category=cat, name=f"Item {c}-{j}",
                price=Decimal("50.00") + 10 * j, sort_order=j,
                available_at_table=True, available_for_pickup=True,
            ))
    staff = {}
    for k, role in enumerate(ROLES):
        tg = 800000000 + i * 10 + k
        user = User.objects.create(
            username=f"lt{i}_{role}", password=PASSWORD, partner=partner, role=role
        )
        account = TelegramAccount.objects.create(telegram_id=tg, language_code="uk")
        EmployeeProfile.objects.create(
            partner=partner, user=user, telegram_account=account, is_active=True,
            bot_notifications_enabled=True,
            notify_on_order_created=role in ("owner", "manager", "cashier"),
            notify_on_order_status_changed=role in ("manager",),
            notify_on_guest_calls=True, notify_on_billing_requests=role != "owner",
        )
        staff[role] = tg
    BonusProgram.objects.create(
        partner=partner, name="Cashback 5%",
        trigger_event=BonusProgram.TriggerEvent.ORDER_COMPLETED,
        program_type=BonusProgram.ProgramType.CASHBACK, percent=Decimal("5.00"),
        min_order_total=Decimal("100.00"), max_redeem_share=Decimal("30.00"),
        expires_in_days=90, config={}, is_active=True,
    )
    return {
        "i": i, "token": tok, "partner_id": str(partner.id),
        "tables": [t.deep_link_payload for t in tables], "staff": staff,
    }


from apps.partners.bot_texts import BUTTON_TEXTS  # noqa: E402
from core.i18n import gettext_in  # noqa: E402

existing = Partner.objects.filter(slug__startswith="lt-").count()
data = json.loads(OUT.read_text())["partners"] if OUT.exists() and existing else []
for i in range(existing, N):
    data.append(make(i))
labels = {
    key: gettext_in("uk", BUTTON_TEXTS[f"button_{key}_label"])
    for key in ("menu", "cart", "request_bill", "pickup", "session")
}
OUT.write_text(json.dumps({"labels": labels, "partners": data}, ensure_ascii=False))
print(f"partners: {len(data)}", labels)
