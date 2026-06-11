from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0003_partnerbotsettings_button_request_bill_label_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="partnerbotsettings",
            name="guest_order_flow",
        ),
    ]
