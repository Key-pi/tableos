from django.db import migrations, models


class Migration(migrations.Migration):
    """ADR-0009: the requested time serves every remote channel, not only pickup."""

    dependencies = [
        ("orders", "0006_sale_channels"),
    ]

    operations = [
        migrations.RenameField(
            model_name="order",
            old_name="pickup_at",
            new_name="requested_for",
        ),
        migrations.AlterField(
            model_name="order",
            name="requested_for",
            field=models.DateTimeField(
                blank=True,
                help_text="Remote orders (pickup, later delivery): the requested time; empty means as soon as possible.",
                null=True,
            ),
        ),
    ]
