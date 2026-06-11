from typing import Protocol


class SessionResolver(Protocol):
    def resolve(self, payload: str) -> object: ...
