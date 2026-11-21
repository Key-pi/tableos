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
- Редактор расчёта стола (PQ-009, шаг 3, `apps/billing/visit_settlement.py`):
  открытые счета визита по id → их `BillItem` → заказы и строки заказов
  (`create_bill_from_lines`) → дальше как оплата; сумма гостя считается
  только после блокировки счетов.
- Вызов персонала «Иду»: только `GuestCall` (`take_guest_call`).
- Приглашение сотрудника: `StaffInvite → EmployeeProfile`.
- Вход в Admin по одноразовой ссылке (PQ-045): только `AdminLoginLink`
  (`apps/users/admin_login.py:redeem_admin_login_link`).
- Экран кухни и бара «Готово»: только `Order` (до чтения `OrderStationReady`).
- Бонусы при общем балансе сети: `GuestProfile` (точки) → `NetworkWallet`.
- Схема зала: `Hall` (`no_key=True`) → `Table` (`floor_plan.save_hall_layout`).
- Бронь: `Table (no_key) → Reservation` (`apps/tables/reservations.py`);
  перенос на другой стол — оба стола по порядку id; «Пришли» затем открывает
  визит через `open_staff_table_session`. «Пришли» для гостя по коду
  (PQ-056): `GuestProfile → Table (no_key) → Reservation` — гостя первым, как
  активация по QR. Напоминание о брони — захват `reminded_at` условным
  `UPDATE`, без блокировки.
- Подключение к Poster (PQ-058): только строка `PosterConnection` заведения
  (`apps/integrations/poster/services.py`); запросы к Poster — до транзакции.
  Одна точка Poster — одно заведение: проверка до сохранения и частичный
  уникальный индекс `poster_connection_active_spot_unique` на гонку двух
  заведений (их строки разные, блокировка не спасает).
  Меню из Poster (`apps/integrations/poster/menu_sync.py:sync_menu`): Poster
  читается до транзакции; затем `PosterConnection` → связанные `MenuItem` по
  id → их `MenuItemVariant` по id (`no_key`, как у заказа), потом остальное
  меню; ссылки читаются после блокировки — вторая синхронизация видит первую.
  Вебхук Poster (`apps/integrations/poster/webhooks.py:process`): строка
  `PosterWebhookEvent` → подключения аккаунта по id (`of=("self",)`);
  статус события перепроверяется под блокировкой. Повтор события — уникальный
  `dedupe_key`, без блокировки. Заказ на кассу
  (`apps/integrations/poster/orders.py`): только строка `PosterOrderPush` —
  захват (`pending`/`unknown` → `sending`) и запись итога, каждое своей
  короткой транзакцией; запрос к Poster между ними, вне транзакции.
- Остатки: строки `MenuItem`/`MenuItemVariant` — последними в транзакции,
  сначала позиции, затем варианты, каждые по id, `no_key=True`
  (`apps/menu/stock.py:_lock`).
- Строку-«родителя», которую блокируют только для очереди (её ключ не
  меняется), блокировать `no_key=True`: `FOR UPDATE` не пускает проверку
  внешних ключей других транзакций при `COMMIT` и даёт deadlock.
- На PostgreSQL `select_for_update` с nullable-join — только с `of=("self",)`.
