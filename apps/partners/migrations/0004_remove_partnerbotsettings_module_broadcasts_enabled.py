from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "partners",
            "0003_remove_partnerbotsettings_allow_multiple_active_sessions_per_guest_and_more",
        ),
    ]

    operations = [
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="module_broadcasts_enabled",
        ),
    ]
