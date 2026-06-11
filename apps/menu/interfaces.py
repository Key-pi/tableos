from typing import Protocol


class CatalogPublisher(Protocol):
    def publish(self, partner_id) -> None: ...
