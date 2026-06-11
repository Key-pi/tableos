from typing import Protocol


class ShiftTracker(Protocol):
    def open_shift(self, employee_id) -> object: ...
