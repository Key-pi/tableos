from apps.partners.models import BotInstance
from core.security.crypto import encrypt_text


def rotate_bot_token(bot_instance: BotInstance, raw_token: str) -> BotInstance:
    bot_instance.token_encrypted = encrypt_text(raw_token)
    bot_instance.save(update_fields=["token_encrypted", "updated_at"])
    return bot_instance
