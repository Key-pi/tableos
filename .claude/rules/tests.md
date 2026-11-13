---
paths:
  - "tests/**"
---

# Тесты

Подробно и журнал прогонов: `docs/engineering/TESTING.md`.

- Тесты — только в `tests/`, по модулям кода: `tests/apps/<приложение>/`,
  `tests/bot/`, `tests/core/`, `tests/webapp/`; файлы `test_*.py`.
- Часть набора: `DJANGO_USE_SQLITE=true python3 manage.py test tests.apps.billing`
  или до класса: `… test tests.webapp.test_kitchen.SplitScreensTests`.
- Сначала тест, который падает, потом код. После исправления — проверка
  мутацией: убрать защиту → тест падает; вернуть → проходит. Мутации
  перечислять в строке журнала `TESTING.md`.
- Блокировки и долговечные ограничения — тест на PostgreSQL:
  `TransactionTestCase` с `skipUnless(connection.vendor == "postgresql")`;
  на SQLite такой тест пропускается (список — в `TESTING.md`).
- Новый модуль конструктора — тест, что он работает один и не задевает
  остальные (`tests/apps/billing/test_shipping.py:ModuleIndependenceTests`).
- Celery в тестах eager.
