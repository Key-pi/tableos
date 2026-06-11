from typing import Protocol


class SnapshotBuilder(Protocol):
    def build(self, partner_id, bucket_date) -> object: ...
