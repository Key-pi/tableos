import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bonuses", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="walkinsale",
            name="customer_code_snapshot",
            field=models.CharField(blank=True, max_length=12),
        ),
        migrations.AlterField(
            model_name="walkinsale",
            name="guest",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="walk_in_sales",
                to="users.guestprofile",
            ),
        ),
    ]
