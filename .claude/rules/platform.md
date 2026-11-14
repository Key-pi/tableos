---
paths:
  - "apps/analytics/system_load.py"
  - "core/load_counter.py"
  - "apps/partners/management/**"
  - "infrastructure/**"
  - "core/settings/**"
  - "docker-compose*.yml"
  - ".github/**"
---

# Платформа: нагрузка, подключение бизнеса, запуск

Подробно: `docs/engineering/LOAD_TESTING.md`, `DEPLOYMENT.md`,
`docs/product/ECOSYSTEM_DIRECTION.md`.

- **Нагрузка (PQ-051):** страница «Нагрузка» — только для платформы
  (`apps/analytics/system_load.py`), снимок раз в минуту, хранится 8 дней,
  графики за сутки и неделю; предупреждения — только на странице, в Telegram —
  будущий бот мониторинга (PQ-039). Партнёры считаются по точкам: нагрузку
  дают точки, сеть из трёх точек — три.
- Счётчик запросов и обновлений (`core/load_counter.py`) не должен мешать
  запросу: в памяти, в Redis не чаще раза в 10 с, ошибки глотает.
- Потолок партнёров — `PLATFORM_CAPACITY_PARTNERS` (из `LOAD_TESTING.md`).
- **Подключение бизнеса (PQ-045):** регистрация — в будущем общем боте
  платформы Hub (отдельный сервис для Shop и Master, туда же ошибки PQ-039);
  бизнес работает сразу, владельцу платформы — сообщение; пока бесплатно. В
  Shop — только части для любого входа; ещё не сделаны: создание бизнеса
  командой/API, вход владельца в Admin по одноразовой ссылке, сообщение
  владельцу платформы. Список «что ещё настроить» уже есть —
  `apps/analytics/dashboard.py:setup_steps`.
- Демо-данные: `seed_baseline_demo`, заведения —
  `apps/partners/management/demo_data.py`; токены и Telegram ID — только из
  `.env` (`DEMO_*`), в коде и документах — заглушки.
- CI — `.github/workflows/tests.yml` (подробно — `docs/engineering/TESTING.md`,
  «GitHub Actions»): SQLite и PostgreSQL 16 с Redis 7, Python 3.12 как в
  `infrastructure/docker/Dockerfile`; ключ Fernet создаётся на запуск, секретов
  у задания нет и не добавлять без согласия владельца; Ruff в CI нет (AA-053).
- Продакшен: `docker-compose.prod.yml`, `core.settings.production`,
  зависимости — `requirements.lock` через pip-compile; обновлять зависимости —
  только с согласия владельца.
