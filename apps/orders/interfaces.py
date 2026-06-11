from typing import Protocol


class OrderNotifier(Protocol):
    def notify_new_order(self, order_id) -> None: ...
