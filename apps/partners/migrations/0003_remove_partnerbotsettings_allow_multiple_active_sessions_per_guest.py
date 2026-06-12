from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0002_modular_settings"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="allow_multiple_active_sessions_per_guest",
        ),
    ]
