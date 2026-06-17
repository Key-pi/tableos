import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0002_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bill",
            name="table",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="bills",
                to="tables.table",
            ),
        ),
        migrations.DeleteModel(
            name="FiscalReceipt",
        ),
    ]
