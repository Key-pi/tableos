"""Attach open bills to the visit of their orders when all orders share one visit."""

from django.db import migrations

OPEN_STATUSES = ("draft", "issued", "partially_paid")


def forwards(apps, schema_editor):
    Bill = apps.get_model("billing", "Bill")
    BillOrder = apps.get_model("billing", "BillOrder")

    for bill in Bill.objects.filter(status__in=OPEN_STATUSES, visit__isnull=True).iterator():
        visit_ids = set(
            BillOrder.objects.filter(bill_id=bill.id).values_list(
                "order__table_session__visit_id",
                flat=True,
            )
        )
        if len(visit_ids) == 1:
            (visit_id,) = visit_ids
            if visit_id is not None:
                Bill.objects.filter(id=bill.id).update(visit_id=visit_id)


def backwards(apps, schema_editor):
    Bill = apps.get_model("billing", "Bill")
    Bill.objects.filter(visit__isnull=False).update(visit=None)


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0006_bill_visit"),
        ("tables", "0005_backfill_table_visits"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
