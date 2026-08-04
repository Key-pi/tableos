from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.partners.models import BotInstance, Partner
from bot.middlewares.partner_runtime import (
    SUSPENDED_PARTNER_MESSAGE,
    PartnerRuntimeMiddleware,
)
from bot.services.context import (
    BotRuntimeDisabledError,
    PartnerSuspendedError,
    resolve_partner_for_bot_token,
)
from bot.services.runtime import BotRuntimeConfig, register_runtime_configs


class _Message:
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class _CallbackQuery:
    def __init__(self) -> None:
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str, show_alert: bool) -> None:
        self.answers.append((text, show_alert))


class PartnerRuntimeGuardTests(TestCase):
    token = "runtime-guard-token"

    def setUp(self):
        self.partner = Partner.objects.create(
            name="Runtime Guard Venue",
            slug="runtime-guard-venue",
            status=Partner.Status.ACTIVE,
        )
        self.bot_instance = BotInstance.objects.create(
            partner=self.partner,
            display_name="Runtime Guard Bot",
            username="runtime_guard_bot",
            mode=BotInstance.Mode.POLLING,
            is_active=True,
            token_encrypted="",
        )
        register_runtime_configs(
            [
                BotRuntimeConfig(
                    partner_id=str(self.partner.id),
                    bot_instance_id=str(self.bot_instance.id),
                    username=self.bot_instance.username,
                    token=self.token,
                )
            ]
        )

    def tearDown(self):
        register_runtime_configs([])

    def test_live_suspension_rejects_partner_resolution(self):
        self.partner.status = Partner.Status.SUSPENDED
        self.partner.save(update_fields=["status"])

        with self.assertRaises(PartnerSuspendedError):
            resolve_partner_for_bot_token(self.token)

    def test_live_bot_deactivation_rejects_partner_resolution(self):
        self.bot_instance.is_active = False
        self.bot_instance.save(update_fields=["is_active"])

        with self.assertRaises(BotRuntimeDisabledError):
            resolve_partner_for_bot_token(self.token)

    def test_suspended_partner_receives_the_approved_message_before_handlers(self):
        self.partner.status = Partner.Status.SUSPENDED
        self.partner.save(update_fields=["status"])
        message = _Message()
        event = SimpleNamespace(message=message, callback_query=None)
        handler = AsyncMock()

        async_to_sync(PartnerRuntimeMiddleware().__call__)(
            handler,
            event,
            {"bot": SimpleNamespace(token=self.token)},
        )

        self.assertEqual(message.answers, [SUSPENDED_PARTNER_MESSAGE])
        handler.assert_not_awaited()

    def test_suspended_partner_callback_receives_an_alert_before_handlers(self):
        self.partner.status = Partner.Status.SUSPENDED
        self.partner.save(update_fields=["status"])
        callback_query = _CallbackQuery()
        event = SimpleNamespace(message=None, callback_query=callback_query)
        handler = AsyncMock()

        async_to_sync(PartnerRuntimeMiddleware().__call__)(
            handler,
            event,
            {"bot": SimpleNamespace(token=self.token)},
        )

        self.assertEqual(
            callback_query.answers,
            [(SUSPENDED_PARTNER_MESSAGE, True)],
        )
        handler.assert_not_awaited()

    def test_live_bot_deactivation_drops_the_update_without_a_reply(self):
        self.bot_instance.is_active = False
        self.bot_instance.save(update_fields=["is_active"])
        message = _Message()
        event = SimpleNamespace(message=message, callback_query=None)
        handler = AsyncMock()

        async_to_sync(PartnerRuntimeMiddleware().__call__)(
            handler,
            event,
            {"bot": SimpleNamespace(token=self.token)},
        )

        self.assertEqual(message.answers, [])
        handler.assert_not_awaited()
