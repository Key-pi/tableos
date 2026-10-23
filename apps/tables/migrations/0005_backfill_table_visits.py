"""Group the active sessions of each table into one active visit (owner decision 2).

Closed historical sessions stay without a visit. The reverse step only detaches
sessions and deletes the visits created here, so no session data is lost.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    TableSession = apps.get_model("tables", "TableSession")
    TableVisit = apps.get_model("tables", "TableVisit")

    active_sessions = (
        TableSession.objects.filter(status="active", visit__isnull=True)
        .order_by("partner_id", "table_id", "started_at")
    )
    visits_by_table = {}
    for session in active_sessions.iterator():
        key = (session.partner_id, session.table_id)
        visit = visits_by_table.get(key)
        if visit is None:
            visit = TableVisit.objects.create(
                partner_id=session.partner_id,
                table_id=session.table_id,
                status="active",
            )
            # started_at is auto_now_add; keep the earliest session time.
            TableVisit.objects.filter(id=visit.id).update(started_at=session.started_at)
            visits_by_table[key] = visit
        TableSession.objects.filter(id=session.id).update(visit_id=visit.id)


def backwards(apps, schema_editor):
    TableSession = apps.get_model("tables", "TableSession")
    TableVisit = apps.get_model("tables", "TableVisit")
    TableSession.objects.filter(visit__isnull=False).update(visit=None)
    TableVisit.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tables", "0004_tablevisit_tablesession_visit_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
