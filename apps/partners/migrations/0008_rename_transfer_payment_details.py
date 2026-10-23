from django.db import migrations, models


class Migration(migrations.Migration):
    """ADR-0009: transfer requisites serve every prepaid channel."""

    dependencies = [
        ("partners", "0007_partnerbotsettings_pickup_payment_details_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="partnerbotsettings",
            old_name="pickup_payment_details",
            new_name="transfer_payment_details",
        ),
        migrations.AlterField(
            model_name="partnerbotsettings",
            name="transfer_payment_details",
            field=models.TextField(
                blank=True,
                help_text="Куда гость переводит деньги за заказы с предоплатой (самовывоз, позже доставка): номер карты или IBAN, получатель. Гость видит этот текст перед оплатой.",
                verbose_name="Реквизиты для оплаты переводом",
            ),
        ),
    ]
