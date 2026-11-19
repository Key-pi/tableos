"""Create a business in one command (PQ-045): venue, bot and owner.

    python manage.py create_business --name "Kava" --owner-telegram-id <id> \\
        [--bot-token <token>] [--owner-name "Оля"] [--phone ...] [--timezone Europe/Kyiv]

Without ``--bot-token`` the token is asked for unseen: on the command line it
stays in the shell history. The work is ``apps/partners/onboarding.py``.
"""

import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.partners.onboarding import BusinessError, admin_link, create_business


class Command(BaseCommand):
    help = "Создать бизнес: заведение, его бота и владельца (PQ-045)."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Название бизнеса.")
        parser.add_argument("--owner-telegram-id", required=True, type=int,
                            help="Telegram ID владельца.")
        parser.add_argument("--bot-token", default="",
                            help="Токен бота из @BotFather; без него команда спросит его скрыто.")
        parser.add_argument("--owner-name", default="", help="Имя владельца.")
        parser.add_argument("--phone", default="", help="Контактный телефон бизнеса.")
        parser.add_argument("--timezone", default="Europe/Kyiv", help="Часовой пояс (IANA).")

    def handle(self, *args, **options):
        token = options["bot_token"] or getpass.getpass("Токен бота из @BotFather: ")
        try:
            business = create_business(
                name=options["name"], bot_token=token,
                owner_telegram_id=options["owner_telegram_id"], owner_name=options["owner_name"],
                phone=options["phone"], timezone_name=options["timezone"],
            )
        except BusinessError as exc:
            raise CommandError(str(exc)) from exc
        partner, bot = business.partner, business.bot
        self.stdout.write(self.style.SUCCESS(f"Бизнес «{partner.name}» создан и работает."))
        self.stdout.write(f"Бот: @{bot.username} — заработает в течение минуты.")
        self.stdout.write(
            f"Владелец: {business.owner.get_full_name() or business.owner.username} "
            f"(Telegram ID {options['owner_telegram_id']}) — пишет боту /start, "
            "рабочий режим у него сразу."
        )
        self.stdout.write(
            "В Admin владелец входит без пароля: «Рабочий режим» в боте → «🔐 Войти в Admin»."
        )
        self.stdout.write(f"Заведение в Admin: {admin_link(partner)}")
        self.stdout.write("Все модули выключены: владелец включает нужные в Admin (PQ-053).")
