"""Make photos uploaded before compression small (PQ-048): WebP, 1200 px.

    python manage.py compress_menu_photos [--dry-run]
"""

from django.core.management.base import BaseCommand

from apps.menu.models import MenuItem
from apps.menu.photos import compress_menu_photo


class Command(BaseCommand):
    help = "Re-save uploaded menu photos as small WebP (the old files are removed)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        done = 0
        for item in MenuItem.objects.exclude(image="").exclude(image__endswith=".webp"):
            if options["dry_run"]:
                self.stdout.write(item.image.name)
                done += 1
                continue
            old = item.image.name
            storage = item.image.storage
            if not storage.exists(old):
                continue
            with storage.open(old, "rb") as source:
                content = compress_menu_photo(source)
            item.image.save("photo.webp", content, save=False)
            MenuItem.objects.filter(pk=item.pk).update(image=item.image.name)
            storage.delete(old)
            done += 1
        self.stdout.write(f"{done} photo(s)")
