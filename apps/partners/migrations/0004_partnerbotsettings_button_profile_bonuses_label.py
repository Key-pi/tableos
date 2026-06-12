from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0003_remove_partnerbotsettings_allow_multiple_active_sessions_per_guest"),
    ]

    operations = [
        migrations.AddField(
            model_name="partnerbotsettings",
            name="button_profile_bonuses_label",
            field=models.CharField(default="Бонусы", max_length=64),
        ),
    ]
