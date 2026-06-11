from django.db import migrations


def rename_default_loyalty_button(apps, schema_editor):
    PartnerBotSettings = apps.get_model("partners", "PartnerBotSettings")
    PartnerBotSettings.objects.filter(button_loyalty_label="Бонусы").update(
        button_loyalty_label="Мой профиль"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0004_remove_quick_order_only"),
    ]

    operations = [
        migrations.RunPython(
            rename_default_loyalty_button,
            migrations.RunPython.noop,
        ),
    ]
