from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update a local superuser for development."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--email", default="admin@tableos.local")
        parser.add_argument("--password", default="")

    def handle(self, *args, **options):
        username = options["username"].strip()
        email = options["email"].strip()
        password = options["password"]
        if not username:
            raise CommandError("`--username` must not be empty.")
        if not email:
            raise CommandError("`--email` must not be empty.")
        if not password:
            raise CommandError("`--password` must not be empty.")

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": True,
                "role": user_model.Role.ADMIN,
            },
        )
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.role = user_model.Role.ADMIN
        user.set_password(password)
        user.save()

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} admin user `{username}`."))
