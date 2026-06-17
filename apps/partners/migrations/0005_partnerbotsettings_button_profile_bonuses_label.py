from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0004_remove_partnerbotsettings_module_broadcasts_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="partnerbotsettings",
            name="button_profile_bonuses_label",
            field=models.CharField(default="Бонусы", max_length=64),
        ),
    ]
