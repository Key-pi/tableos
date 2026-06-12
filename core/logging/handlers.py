from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock

_PARTNER_ID_PATTERN = re.compile(r"partner_id=([0-9])")


def _extract_partner_id(record: logging.LogRecord) -> str | None:
    direct_value = getattr(record, "partner_id", None)
    if direct_value and direct_value != "-":
        return str(direct_value)

    message = record.getMessage()
    match = _PARTNER_ID_PATTERN.search(message)
    if match:
        return match.group(1)
    return None


class _RoutingFilter(logging.Filter):
    def __init__(self, *, route: str):
        super().__init__()
        self.route = route

    def filter(self, record: logging.LogRecord) -> bool:
        partner_id = _extract_partner_id(record)
        record.partner_id = partner_id or "-"
        if self.route == "partner":
            return partner_id is not None
        return partner_id is None


class SystemLogFilter(_RoutingFilter):
    def __init__(self):
        super().__init__(route="system")


class PartnerLogFilter(_RoutingFilter):
    def __init__(self):
        super().__init__(route="partner")


class PartnerRoutingFileHandler(logging.Handler):
    def __init__(
        self,
        base_dir: str,
        filename: str = "app.log",
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 5,
    ):
        super().__init__()
        self.base_dir = Path(base_dir)
        self.filename = filename
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._handlers: dict[str, RotatingFileHandler] = {}
        self._lock = RLock()

    def emit(self, record: logging.LogRecord) -> None:
        partner_id = _extract_partner_id(record)
        if partner_id is None:
            return
        handler = self._get_handler(partner_id)
        handler.emit(record)

    def close(self) -> None:
        with self._lock:
            for handler in self._handlers.values():
                handler.close()
            self._handlers.clear()
        super().close()

    def _get_handler(self, partner_id: str) -> RotatingFileHandler:
        with self._lock:
            handler = self._handlers.get(partner_id)
            if handler is not None:
                return handler

            partner_dir = self.base_dir / partner_id
            partner_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                partner_dir / self.filename,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding="utf-8",
            )
            handler.setLevel(self.level)
            if self.formatter is not None:
                handler.setFormatter(self.formatter)
            self._handlers[partner_id] = handler
            return handler
