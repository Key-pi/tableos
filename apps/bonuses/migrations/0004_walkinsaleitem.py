import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bonuses", "0003_walkinsale_customer_code_snapshot_and_guest_nullable"),
        ("menu", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="WalkInSaleItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("item_name", models.CharField(max_length=255)),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=10)),
                ("quantity", models.PositiveIntegerField(default=1)),
                (
                    "menu_item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="walk_in_sale_items",
                        to="menu.menuitem",
                    ),
                ),
                (
                    "partner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)ss",
                        to="partners.partner",
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="bonuses.walkinsale",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "item_name"],
            },
        ),
    ]
