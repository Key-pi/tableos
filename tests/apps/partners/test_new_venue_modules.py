"""A new venue starts with every module off (PQ-053, owner 2026-11-14): the
owner switches on what the business needs, and the Admin start page says so
in «Что ещё настроить». Existing venues keep their values; the demo switches
its modules on explicitly."""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.analytics import dashboard
from apps.partners.models import Partner, PartnerBotSettings
from bot.services.content import BotContent

MODULES = [
    field.name for field in PartnerBotSettings._meta.concrete_fields
    if field.name.startswith("module_")
]


def new_venue(slug="new-venue"):
    partner = Partner.objects.create(name="New venue", slug=slug)
    return partner, PartnerBotSettings.objects.create(partner=partner)


class NewVenueTests(TestCase):
    def test_every_module_is_off(self):
        _, settings = new_venue()
        self.assertEqual(len(MODULES), 20)
        self.assertEqual([m for m in MODULES if getattr(settings, m)], [])

    def test_a_bot_without_a_settings_row_has_them_off_too(self):
        content = BotContent.defaults()
        on = [m for m in MODULES if getattr(content, m, False)]
        self.assertEqual(on, [])

    def test_the_start_page_asks_to_switch_modules_on(self):
        partner, settings = new_venue()
        self.assertEqual(dashboard.setup_steps(partner.id)[:2],
                         [dashboard.SETUP_BOT, dashboard.SETUP_MODULES])
        settings.module_pickup_enabled = True
        settings.save()
        self.assertNotIn(dashboard.SETUP_MODULES, dashboard.setup_steps(partner.id))

    def test_the_step_has_a_title_and_leads_to_the_settings(self):
        from apps.users import admin_dashboard

        partner, settings = new_venue()
        user = get_user_model().objects.create_superuser("root", "root@example.com", "x")
        request = RequestFactory().get("/admin/")
        request.user = user
        request.session = {"admin_partner_id": str(partner.id)}
        steps = admin_dashboard._setup(request, partner.id)
        titles = [step["title"] for step in steps]
        self.assertIn("Включите нужные модули", titles)
        step = steps[titles.index("Включите нужные модули")]
        self.assertIn(str(settings.pk), step["link"] or "")
