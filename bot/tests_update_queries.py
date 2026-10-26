"""Fewer queries per bot update, without caching anything that can be revoked.

Every update re-read the bot and partner twice (middleware and handler) and
the owner's own texts once or more; all database work of a bot process goes
through one thread, so each query adds to every partner's wait (load test
2026-10-25, AA-047).

- Within one update the partner checked at its start is reused: the same
  update, the same answer. The next update checks again, so a suspension or a
  disabled bot still applies from the next update on.
- The owner's texts are cached and dropped whenever a text is saved or
  deleted, so an edit in Admin applies on the next update.
"""

from types import SimpleNamespace

from asgiref.sync import async_to_sync, sync_to_async
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.partners.languages import text_overrides
from apps.partners.models import BotInstance, BotText, Partner
from bot.middlewares.partner_runtime import PartnerRuntimeMiddleware
from bot.services.context import PartnerSuspendedError, resolve_partner_for_bot_token
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs

TOKEN = "update-queries-token"


class _Setup(TestCase):
    def setUp(self):
        cache.clear()
        self.partner = Partner.objects.create(
            name="Queries Venue", slug="queries-venue", status=Partner.Status.ACTIVE
        )
        self.bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Queries Bot",
            username="queries_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=True,
            token_encrypted="",
        )
        register_runtime_configs(
            [
                BotRuntimeConfig(
                    partner_id=str(self.partner.id),
                    bot_instance_id=str(self.bot_instance.id),
                    username="queries_bot",
                    token=TOKEN,
                )
            ]
        )
        self.addCleanup(register_runtime_configs, [])

    def _update(self, handler):
        return async_to_sync(PartnerRuntimeMiddleware().__call__)(
            handler, SimpleNamespace(message=None, callback_query=None),
            {"bot": SimpleNamespace(token=TOKEN)},
        )


class PartnerWithinOneUpdateTests(_Setup):
    def test_handler_reuses_the_partner_checked_for_this_update(self):
        seen = {}

        def resolve_counting():
            with CaptureQueriesContext(connection) as queries:
                partner = resolve_partner_for_bot_token(TOKEN)
            seen["partner"], seen["queries"] = partner, len(queries)

        async def handler(_event, _data):
            await sync_to_async(resolve_counting)()

        self._update(handler)

        self.assertEqual(seen["partner"].id, self.partner.id)
        self.assertEqual(seen["queries"], 0)

    def test_next_update_checks_the_partner_again(self):
        self._update(lambda _event, _data: sync_to_async(lambda: None)())
        Partner.objects.filter(id=self.partner.id).update(status=Partner.Status.SUSPENDED)

        with self.assertRaises(PartnerSuspendedError):
            resolve_partner_for_bot_token(TOKEN)


class OwnerTextsCacheTests(_Setup):
    def test_texts_are_read_once_until_changed(self):
        BotText.objects.create(partner=self.partner, language="uk", msgid="Меню", text="Карта")
        self.assertEqual(text_overrides(self.partner.id), {("uk", "Меню"): "Карта"})
        with CaptureQueriesContext(connection) as queries:
            text_overrides(self.partner.id)
        self.assertEqual(len(queries), 0)

    def test_saving_or_deleting_a_text_applies_on_the_next_read(self):
        text = BotText.objects.create(
            partner=self.partner, language="uk", msgid="Меню", text="Карта"
        )
        text_overrides(self.partner.id)

        text.text = "Наше меню"
        text.save()
        self.assertEqual(text_overrides(self.partner.id), {("uk", "Меню"): "Наше меню"})

        BotText.objects.filter(id=text.id).delete()
        self.assertEqual(text_overrides(self.partner.id), {})

    def test_partners_do_not_share_cached_texts(self):
        other = Partner.objects.create(name="Other", slug="other-queries")
        BotText.objects.create(partner=self.partner, language="uk", msgid="Меню", text="Карта")
        text_overrides(self.partner.id)
        self.assertEqual(text_overrides(other.id), {})
