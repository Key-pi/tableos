from typing import Protocol


class TelegramIdentityProvider(Protocol):
    def sync_profile(self, telegram_id: int) -> object: ...
