---
paths:
  - "apps/notifications/**"
  - "apps/analytics/**"
  - "bot/handlers/start.py"
  - "tests/apps/notifications/**"
  - "tests/apps/analytics/**"
---

# Источники переходов, рассылки, уведомления

Подробно: `docs/architecture/BACKGROUND_JOBS.md`, ADR-0008.

- **Источники переходов (PQ-031, `start=src_…`):** гость закрепляется за первым
  источником, все переходы пишутся; источники создаёт владелец в Admin;
  цифры — только переходы и новые гости, только в Admin.
- **Доставка сообщений (ADR-0008):** рассылки — не больше одного раза на гостя
  (`BroadcastDelivery`); рабочие уведомления — хотя бы один раз. Сеть Telegram
  и паузы — не внутри транзакции (`AGENTS.md`).
- Уведомления — на языке получателя: текст внутри `speaking_to_employee(...)`
  / `speaking_to_guest(...)` (`i18n.md`).
- Анонимному гостю визита (без `telegram_account`) не пишем и рассылок не шлём.
