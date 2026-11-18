---
paths:
  - "apps/**/*.py"
  - "webapp/**/*.py"
  - "tests/**/*.py"
---

# Порядок блокировок (не менять без причины)

Любое новое или изменённое `select_for_update` — тест на PostgreSQL и полный
прогон на PostgreSQL (SQLite блокировки игнорирует, AA-033).

- Оплата: `Bill → Order → GuestProfile → BillingRequest → TableSession → TableVisit`.
- Активация стола: `GuestProfile → Table → свои TableSession → TableVisit`
  (стол — `select_for_update(no_key=True)`, AA-040).
- Заказ на стол с планшета — как активация:
  `GuestProfile → Table (no_key) → свои TableSession → TableVisit`
  (`apps/tables/services.py:open_staff_table_session`). Гость уже в этой
  компании — его сессия берётся под блокировкой и остаётся; ещё один гость
  без бота (`new_guest`) — новая строка под блокировкой стола. «Гостей за
  столом» — условный `UPDATE` одного столбца визита, без блокировки
  (`set_visit_guests_count`).
- Истечение пустой сессии стола (PQ-008): активные корзины гостя →
  `TableSession` → `TableVisit` (`apps/tables/services.py:_expire_idle_session`).
  Оформление заказа за столом тоже сначала блокирует корзину и после этого
  перепроверяет, что сессия ещё `active` — так оно не проскочит в закрытую.
- Продажа на кассе (PQ-054) — как удалённый заказ: названный `GuestProfile` →
  новый заказ и строки остатков → `Bill` → `Order` → `GuestProfile` (уже
  удержан) (`apps/billing/counter_sales.py`); повтор — уникальный
  `Order.request_key`.
- Изменение счёта и возврат: `Bill → BillItem → GuestProfile → Order`.
- Вызов персонала «Иду»: только `GuestCall` (`take_guest_call`).
- Приглашение сотрудника: `StaffInvite → EmployeeProfile`.
- Экран кухни и бара «Готово»: только `Order` (до чтения `OrderStationReady`).
- Бонусы при общем балансе сети: `GuestProfile` (точки) → `NetworkWallet`.
- Схема зала: `Hall` (`no_key=True`) → `Table` (`floor_plan.save_hall_layout`).
- Бронь: `Table (no_key) → Reservation` (`apps/tables/reservations.py`);
  перенос на другой стол — оба стола по порядку id; «Пришли» затем открывает
  визит через `open_staff_table_session`. «Пришли» для гостя по коду
  (PQ-056): `GuestProfile → Table (no_key) → Reservation` — гостя первым, как
  активация по QR. Напоминание о брони — захват `reminded_at` условным
  `UPDATE`, без блокировки.
- Остатки: строки `MenuItem`/`MenuItemVariant` — последними в транзакции,
  сначала позиции, затем варианты, каждые по id, `no_key=True`
  (`apps/menu/stock.py:_lock`).
- Строку-«родителя», которую блокируют только для очереди (её ключ не
  меняется), блокировать `no_key=True`: `FOR UPDATE` не пускает проверку
  внешних ключей других транзакций при `COMMIT` и даёт deadlock.
- На PostgreSQL `select_for_update` с nullable-join — только с `of=("self",)`.
