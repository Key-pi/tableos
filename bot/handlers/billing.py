from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.billing.models import BillingRequest
from apps.billing.services import BillingServiceError
from apps.billing.table_bills import create_billing_request_for_telegram_user
from apps.tables.services import get_active_table_session
from bot.filters.content import PartnerButtonFilter
from bot.keyboards.billing import build_billing_request_keyboard
from bot.keyboards.main import build_main_keyboard
from bot.services.content import BotContent
from bot.services.context import resolve_partner_for_bot_token
from bot.services.navigation import resolve_guest_navigation_state
from core.i18n import _

router = Router()


async def _resolve_partner_and_content(bot: Bot):
    partner = await sync_to_async(resolve_partner_for_bot_token)(bot.token)
    content = await sync_to_async(BotContent.for_partner)(partner)
    return partner, content


@router.message(PartnerButtonFilter("button_request_bill_label"))
async def request_bill_handler(message: Message, bot: Bot) -> None:
    partner, content = await _resolve_partner_and_content(bot)
    navigation_state = await sync_to_async(resolve_guest_navigation_state)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
        content=content,
    )
    if not content.supports_billing_request():
        await message.answer(
            _("Запрос счёта сейчас отключён для этого бота."),
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    session = await sync_to_async(get_active_table_session)(
        partner_id=partner.id,
        telegram_id=message.from_user.id,
    )
    if session is None:
        await message.answer(
            _("Запросить счёт можно за столом: отсканируйте QR-код на столе."),
            reply_markup=build_main_keyboard(content, navigation_state=navigation_state),
        )
        return

    await message.answer(
        "\n".join(
            [
                content.request_bill_prompt_message(),
                _("Активный стол: #{number}", number=session.table.number),
            ]
        ),
        reply_markup=build_billing_request_keyboard(),
    )


@router.callback_query(F.data.startswith("billreq:"))
async def request_bill_callback(callback: CallbackQuery, bot: Bot) -> None:
    partner, content = await _resolve_partner_and_content(bot)
    if not content.supports_billing_request():
        await callback.answer(_("Запрос счёта отключён."), show_alert=True)
        return

    request_type = (callback.data or "").split(":", 1)[1]
    if request_type == "cancel":
        await callback.message.edit_text(_("Запрос счёта отменён."))
        await callback.answer()
        return

    try:
        result = await sync_to_async(create_billing_request_for_telegram_user)(
            partner_id=partner.id,
            telegram_id=callback.from_user.id,
            request_type=request_type,
            cooldown_seconds=content.duplicate_request_cooldown_seconds,
        )
    except BillingServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if request_type == BillingRequest.RequestType.PERSONAL and result.bill is not None:
        text = content.request_bill_personal_success_message(
            partner_name=partner.name,
            table_number=result.request.table.number,
            bill_public_id=result.bill.public_id,
        )
    elif request_type == BillingRequest.RequestType.SHARED and result.bill is not None:
        text = content.request_bill_shared_success_message(
            partner_name=partner.name,
            table_number=result.request.table.number,
            bill_public_id=result.bill.public_id,
        )
    else:
        text = content.request_bill_custom_success_message(
            partner_name=partner.name,
            table_number=result.request.table.number,
        )

    lines = [text]
    if result.bill is not None:
        lines.append(_("К оплате: <b>{total} грн</b>.", total=result.bill.total_amount))
    lines.append(_("Персонал уже знает и подойдёт к столу."))
    text = "\n".join(lines)

    await callback.message.edit_text(text)
    await callback.answer(_("Запрос счёта отправлен."))
