from typing import Protocol


class NotificationGateway(Protocol):
    def send(self, chat_id: int, text: str) -> None: ...
