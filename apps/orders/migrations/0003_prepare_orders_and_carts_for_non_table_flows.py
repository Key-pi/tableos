import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0002_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cart",
            name="table_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="carts",
                to="tables.tablesession",
            ),
        ),
        migrations.AlterField(
            model_name="order",
            name="table",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="tables.table",
            ),
        ),
        migrations.AlterField(
            model_name="order",
            name="table_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="tables.tablesession",
            ),
        ),
        migrations.AddConstraint(
            model_name="cart",
            constraint=models.UniqueConstraint(
                condition=Q(status="active"),
                fields=("partner", "guest"),
                name="unique_active_cart_per_partner_guest",
            ),
        ),
    ]
