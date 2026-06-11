from typing import Protocol


class PartnerScopedService(Protocol):
    def __call__(self, partner_id: str) -> object: ...
