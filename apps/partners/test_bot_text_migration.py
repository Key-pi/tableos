"""Texts that were settings fields move to the owner's own texts (PQ-040)."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("partners", "0015_languages")]
AFTER = [("partners", "0016_bot_texts_from_settings")]


class BotTextMigrationTests(TransactionTestCase):
    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(BEFORE)
        apps = self.executor.loader.project_state(BEFORE).apps
        Partner = apps.get_model("partners", "Partner")
        Settings = apps.get_model("partners", "PartnerBotSettings")
        self.partner = Partner.objects.create(name="M", slug="migr-texts", status="active")
        Settings.objects.create(
            partner=self.partner,
            default_language="ru",
            button_menu_label="Меню бара",
            button_cart_label="Корзина",  # the default: nothing to keep
            welcome_message_template="Привет в {partner_name}!",
            ordering_help_message_template="Звоните нам.",
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def test_own_values_become_texts_in_the_bots_language_and_back(self):
        apps = self._migrate(AFTER)
        BotText = apps.get_model("partners", "BotText")
        texts = dict(
            BotText.objects.filter(partner_id=self.partner.id, language="ru").values_list(
                "msgid", "text"
            )
        )
        self.assertEqual(
            texts,
            {
                "Открыть меню": "Меню бара",
                "Добро пожаловать в {partner_name}.": "Привет в {partner_name}!",
                "Свой текст «Как заказать» целиком вместо собранного ботом": "Звоните нам.",
            },
        )
        apps = self._migrate(BEFORE)
        settings = apps.get_model("partners", "PartnerBotSettings").objects.get(
            partner_id=self.partner.id
        )
        self.assertEqual(settings.button_menu_label, "Меню бара")
        self.assertEqual(settings.welcome_message_template, "Привет в {partner_name}!")
        self.assertEqual(settings.ordering_help_message_template, "Звоните нам.")
