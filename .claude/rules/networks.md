---
paths:
  - "apps/partners/networks.py"
  - "apps/partners/admin_network.py"
  - "apps/employees/network_staff.py"
  - "apps/bonuses/**"
  - "bot/middlewares/**"
  - "tests/apps/partners/**"
---

# Сети заведений (PQ-047)

Подробно: `docs/architecture/NETWORKS.md`.

- «Сеть» — над заведениями; точка остаётся партнёром, граница данных не
  меняется.
- Один вход владельца с переключателем точки, менеджер на несколько точек
  (`extra_partners`), отчёт по сети. Действия за точку — `acts_for_partner` /
  `owns_partner`, не `user.partner_id`.
- Один бот на сеть (`Network.bot`, выбирает владелец в карточке сети): точку
  обновления выбирает middleware, обработчики о сети не знают; бот для
  точки — `bot_for_partner`.
- Общие бонусы (`Network.bonus_mode`): журнал — у точки, баланс — сумма по
  сети, замок `NetworkWallet`; баланс показывать через `bonus_balance(guest)`,
  гостя по коду искать через `find_guest_by_customer_code`.
- Владелец и менеджеры сети в боте других точек — копии профиля
  (`EmployeeProfile.mirrored_from`); руками не трогать, их держит
  `sync_network_staff`.
- Рассылки и источники переходов — по сети.
