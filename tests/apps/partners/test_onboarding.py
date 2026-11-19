"""A business in one command (PQ-045, owner 2026-11-04 and 2026-11-19): the
venue, its settings with every module off (PQ-053), its bot and its owner
with Admin; the business works at once and the platform owner gets a message
through the platform bot. The token is checked with Telegram before anything
is written."""

from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.employees.models import EmployeeProfile
from apps.partners import onboarding
from apps.partners.models import BotInstance, Partner, PartnerBotSettings
from apps.partners.onboarding import BusinessError, create_business
from apps.users.models import AdminAccessProfile, TelegramAccount, User
from tests.apps.tables.test_reservation_guest import Sent

TOKEN = "444444444:AAEkavakavakavakavakavakavakavaka"
OTHER_TOKEN = "555555555:AAEotherotherotherotherotherother"
OWNER_TG = 7709001
PLATFORM_TG = 7709999
PLATFORM_TOKEN = "666666666:AAEplatformplatformplatformplatfo"


def telegram_knows(username="kava_shop_bot"):
    return patch.object(onboarding, "fetch_bot_username", return_value=username)


@override_settings(PLATFORM_BOT_TOKEN=PLATFORM_TOKEN, PLATFORM_OWNER_TELEGRAM_ID=PLATFORM_TG)
class CreateBusinessTests(TestCase):
    def setUp(self):
        self.sent = Sent()
        patcher = patch("apps.notifications.services._send_telegram_messages", new=self.sent)
        patcher.start()
        self.addCleanup(patcher.stop)

    def create(self, **changes):
        params = {"name": "Kava & Co", "bot_token": TOKEN, "owner_telegram_id": OWNER_TG,
                  "owner_name": "Оля", "phone": "+380501112233"}
        params.update(changes)
        with self.captureOnCommitCallbacks(execute=True):
            return create_business(**params)

    def test_the_venue_works_at_once_with_every_module_off(self):
        with telegram_knows():
            business = self.create()
        partner = Partner.objects.get(id=business.partner.id)
        self.assertEqual((partner.name, partner.status, partner.contact_phone, partner.timezone),
                         ("Kava & Co", Partner.Status.ACTIVE, "+380501112233", "Europe/Kyiv"))
        settings = PartnerBotSettings.objects.get(partner=partner)
        modules = [field.name for field in PartnerBotSettings._meta.concrete_fields
                   if field.name.startswith("module_") and getattr(settings, field.name)]
        self.assertEqual(modules, [])
        bot = BotInstance.objects.get(partner=partner)
        self.assertEqual((bot.username, bot.token, bot.mode, bot.is_active),
                         ("kava_shop_bot", TOKEN, BotInstance.Mode.POLLING, True))

    def test_the_owner_has_the_bot_and_admin_without_a_password(self):
        with telegram_knows():
            business = self.create()
        owner = User.objects.get(id=business.owner.id)
        self.assertEqual((owner.role, owner.partner_id, owner.first_name, owner.is_staff),
                         (User.Role.OWNER, business.partner.id, "Оля", True))
        self.assertFalse(owner.has_usable_password())
        access = AdminAccessProfile.objects.get(user=owner)
        self.assertTrue(access.is_partner_owner and access.can_access_admin and access.is_active)
        employee = EmployeeProfile.objects.get(user=owner)
        self.assertEqual((employee.partner_id, employee.telegram_account.telegram_id),
                         (business.partner.id, OWNER_TG))

    def test_the_platform_owner_is_told(self):
        with telegram_knows():
            self.create()
        texts = self.sent.texts(PLATFORM_TG)
        self.assertEqual(len(texts), 1)
        self.assertIn("Kava &amp; Co", texts[0])
        self.assertIn("@kava_shop_bot", texts[0])
        self.assertIn(str(OWNER_TG), texts[0])
        self.assertEqual(self.sent.texts(OWNER_TG), [])

    @override_settings(PLATFORM_BOT_TOKEN="", PLATFORM_OWNER_TELEGRAM_ID=None)
    def test_without_the_platform_bot_nobody_is_told(self):
        with telegram_knows():
            business = self.create()
        self.assertTrue(Partner.objects.filter(id=business.partner.id).exists())
        self.assertEqual(self.sent.messages, [])

    def test_a_connected_bot_is_not_connected_twice(self):
        with telegram_knows():
            first = self.create()
        with (telegram_knows("KAVA_shop_bot"),
              self.assertRaisesMessage(BusinessError, "@kava_shop_bot")):
            self.create(name="Second", bot_token=OTHER_TOKEN)
        self.assertEqual(list(Partner.objects.values_list("id", flat=True)), [first.partner.id])

    def test_a_bot_taken_meanwhile_leaves_nothing_behind(self):
        # Two commands at once: both saw the bot free; the database lets one in.
        with telegram_knows():
            self.create()
        with (telegram_knows(), patch.object(onboarding, "_connected_bot", return_value=None),
              self.assertRaises(BusinessError)):
            self.create(name="Second", bot_token=OTHER_TOKEN)
        self.assertEqual(Partner.objects.count(), 1)
        self.assertEqual(User.objects.filter(role=User.Role.OWNER).count(), 1)

    def test_telegram_refusing_the_token_writes_nothing(self):
        with (patch.object(onboarding, "fetch_bot_username",
                           side_effect=BusinessError("Telegram не принял токен бота.")),
              self.assertRaisesMessage(BusinessError, "Telegram не принял токен")):
            self.create()
        self.assertFalse(Partner.objects.exists())
        self.assertFalse(TelegramAccount.objects.filter(telegram_id=OWNER_TG).exists())

    def test_wrong_input_is_refused_before_asking_telegram(self):
        cases = [
            ({"name": "  "}, "название"),
            ({"bot_token": "not-a-token"}, "токен"),
            ({"owner_telegram_id": 0}, "Telegram ID"),
            ({"timezone_name": "Mars/Base"}, "часовой пояс"),
        ]
        for changes, words in cases:
            with (self.subTest(changes),
                  patch.object(onboarding, "fetch_bot_username") as asked,
                  self.assertRaisesMessage(BusinessError, words)):
                self.create(**changes)
            asked.assert_not_called()
        self.assertFalse(Partner.objects.exists())

    def test_an_owner_of_another_business_gets_a_login_of_their_own(self):
        with telegram_knows():
            first = self.create()
        with telegram_knows("second_shop_bot"):
            second = self.create(name="Second", bot_token=OTHER_TOKEN, owner_name="")
        self.assertNotEqual(first.owner.id, second.owner.id)
        self.assertEqual(TelegramAccount.objects.filter(telegram_id=OWNER_TG).count(), 1)
        self.assertEqual(
            set(EmployeeProfile.objects.filter(telegram_account__telegram_id=OWNER_TG)
                .values_list("partner_id", flat=True)),
            {first.partner.id, second.partner.id},
        )
        # Without a name the owner keeps the one Telegram gave.
        self.assertEqual(User.objects.get(id=second.owner.id).first_name, "Оля")

    def test_the_code_comes_from_the_bot_when_the_name_has_no_latin(self):
        Partner.objects.create(name="Taken", slug="kava_shop_bot")
        with telegram_knows():
            business = self.create(name="Кава")
        self.assertEqual(Partner.objects.get(id=business.partner.id).slug, "kava_shop_bot-2")


@override_settings(PLATFORM_BOT_TOKEN="", PLATFORM_OWNER_TELEGRAM_ID=None)
class CreateBusinessCommandTests(TestCase):
    def test_the_command_creates_the_business_and_says_what_next(self):
        out = StringIO()
        with telegram_knows():
            call_command("create_business", "--name", "Kava", "--bot-token", TOKEN,
                         "--owner-telegram-id", str(OWNER_TG), "--owner-name", "Оля", stdout=out)
        partner = Partner.objects.get(name="Kava")
        self.assertIn("@kava_shop_bot", out.getvalue())
        self.assertIn(f"/admin/partners/partner/{partner.id}/change/", out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())

    def test_the_token_can_be_typed_unseen(self):
        # A token on the command line stays in the shell history.
        with telegram_knows(), patch("getpass.getpass", return_value=TOKEN) as typed:
            call_command("create_business", "--name", "Kava", "--owner-telegram-id", str(OWNER_TG),
                         stdout=StringIO())
        typed.assert_called_once()
        self.assertEqual(BotInstance.objects.get().token, TOKEN)

    def test_a_refusal_is_a_command_error(self):
        with telegram_knows(), self.assertRaisesMessage(CommandError, "Telegram ID"):
            call_command("create_business", "--name", "Kava", "--bot-token", TOKEN,
                         "--owner-telegram-id", "-5", stdout=StringIO())
