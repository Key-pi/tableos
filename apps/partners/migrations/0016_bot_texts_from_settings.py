import hashlib

from django.db import migrations

# Texts that were fields of PartnerBotSettings move to the owner's own texts
# (BotText, PQ-040), keyed by the catalog text they replace. Frozen here: the
# catalog may change later, this mapping must not.
BUTTONS = {
    "button_menu_label": "Открыть меню",
    "button_session_label": "Мой стол",
    "button_cart_label": "Корзина",
    "button_checkout_label": "Оформить заказ",
    "button_delivery_label": "Заказать доставку",
    "button_pickup_label": "Самовывоз",
    "button_shipping_label": "Отправка почтой",
    "button_help_label": "Как заказать",
    "button_my_profile": "Мой профиль",
    "button_profile_bonuses_label": "Бонусы",
    "button_call_staff_label": "Позвать персонал",
    "button_request_bill_label": "Запросить счёт",
    "button_call_waiter_label": "Официант",
    "button_call_bartender_label": "Бар / касса",
    "button_call_hookah_label": "Кальянщик",
}
TEMPLATES = {
    "welcome_message_template": "Добро пожаловать в {partner_name}.",
    "menu_header_template": "<b>Меню {partner_name}</b>",
    "menu_requires_session_hint_template": (
        "Меню можно смотреть уже сейчас.\n"
        "Чтобы оформить заказ, сначала отсканируйте QR-код вашего стола."
    ),
    "menu_active_session_hint_template": "Активный стол: #{table_number}",
    "menu_empty_message_template": "Сейчас в меню нет доступных позиций.",
    "cart_empty_message_template": (
        "Корзина пока пуста. Откройте меню и добавьте позиции кнопками."
    ),
    "cart_cleared_message_template": "Корзина очищена.",
    "staff_call_prompt_template": "Кого нужно позвать к столу? Выберите нужный вариант ниже.",
    "staff_call_success_template": (
        "{call_target_label} уже получает уведомление по столу #{table_number}."
    ),
    "request_bill_prompt_template": (
        "Как вы хотите оформить счёт? Выберите подходящий вариант ниже."
    ),
    "request_bill_personal_success_template": (
        "Подготовили персональный счёт #{bill_public_id} по столу #{table_number}."
    ),
    "request_bill_shared_success_template": (
        "Подготовили общий счёт #{bill_public_id} по столу #{table_number}."
    ),
    "request_bill_custom_success_template": (
        "Запрос на нестандартное разделение счёта по столу #{table_number} отправлен персоналу."
    ),
    "order_created_message_template": (
        "Заказ принят: #{order_public_id}\n"
        "Сумма: {total_amount} грн\n"
        "Мы уже передали его персоналу."
    ),
    "table_activated_message_template": (
        "Свой текст «Стол открыт» целиком вместо собранного ботом. "
        "Подстановки: {partner_name}, {table_number}"
    ),
    "ordering_help_message_template": (
        "Свой текст «Как заказать» целиком вместо собранного ботом"
    ),
}
FIELDS = {**BUTTONS, **TEMPLATES}


def _key(msgid):
    return hashlib.sha1(msgid.encode("utf-8")).hexdigest()


def settings_to_texts(apps, schema_editor):
    Settings = apps.get_model("partners", "PartnerBotSettings")
    BotText = apps.get_model("partners", "BotText")
    for settings in Settings.objects.all():
        for field, msgid in FIELDS.items():
            value = (getattr(settings, field) or "").strip()
            if not value or value == msgid:
                continue
            BotText.objects.update_or_create(
                partner_id=settings.partner_id,
                language=settings.default_language,
                key=_key(msgid),
                defaults={"msgid": msgid, "text": value},
            )


def texts_to_settings(apps, schema_editor):
    Settings = apps.get_model("partners", "PartnerBotSettings")
    BotText = apps.get_model("partners", "BotText")
    for settings in Settings.objects.all():
        own = dict(
            BotText.objects.filter(
                partner_id=settings.partner_id, language=settings.default_language
            ).values_list("msgid", "text")
        )
        changed = []
        for field, msgid in FIELDS.items():
            if msgid in own:
                setattr(settings, field, own[msgid])
                changed.append(field)
        if changed:
            settings.save(update_fields=changed)


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0015_languages"),
    ]

    operations = [
        migrations.RunPython(settings_to_texts, texts_to_settings),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_call_bartender_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_call_hookah_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_call_staff_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_call_waiter_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_cart_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_checkout_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_delivery_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_help_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_menu_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_my_profile",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_pickup_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_profile_bonuses_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_request_bill_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_session_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="button_shipping_label",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="cart_cleared_message_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="cart_empty_message_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="menu_active_session_hint_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="menu_empty_message_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="menu_header_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="menu_requires_session_hint_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="order_created_message_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="ordering_help_message_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="request_bill_custom_success_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="request_bill_personal_success_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="request_bill_prompt_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="request_bill_shared_success_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="staff_call_prompt_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="staff_call_success_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="table_activated_message_template",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="welcome_message_template",
        ),
    ]
