from typing import Protocol


class BonusCalculator(Protocol):
    def calculate(self, order_total) -> object: ...
