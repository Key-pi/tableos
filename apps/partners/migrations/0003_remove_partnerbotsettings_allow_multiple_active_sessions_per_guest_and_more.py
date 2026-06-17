from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0002_modular_settings"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="allow_multiple_active_sessions_per_guest",
        ),
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="module_loyalty_enabled",
        ),
        migrations.AlterField(
            model_name="partnerbotsettings",
            name="module_quick_sale_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Продажа на кассе по коду клиента. Требует меню.",
                verbose_name="Быстрые продажи",
            ),
        ),
    ]
