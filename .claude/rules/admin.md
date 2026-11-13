---
paths:
  - "apps/*/admin*.py"
  - "apps/analytics/dashboard.py"
  - "core/admin_mixins.py"
  - "core/labels.py"
  - "templates/**"
  - "tests/**/test_*admin*.py"
---

# Django Admin (PQ-050)

Подробно: `docs/architecture/AS_IS_ARCHITECTURE.md` (раздел Admin). Доступ к
Admin не равен правам сотрудника (`AGENTS.md`).

- Ориентир — планшет, главное — удобная навигация.
- Меню — по задачам заведения (`apps/users/admin_navigation.py`): новую
  страницу добавлять туда (группа, иконка, «+», модуль, вкладки). Служебная
  таблица — `service_table = True` и вкладкой своей страницы; запись «одна на
  заведение» — `single_record`.
- Стартовая страница заведения — `apps/users/admin_dashboard.py` (счёт —
  `apps/analytics/dashboard.py`): карточка видна по правам страницы, куда
  ведёт, и модулю; ссылка открывает ровно посчитанное.
- Кнопки действий записи — `ObjectActionsMixin` (POST, проверка заново, сервис
  владельца); кнопки заказа брать из `staff_order_actions`, не придумывать
  свои.
- Подпись записи с заведением — через `core/labels.py` (`venue_label` /
  `within_one_venue`): в Admin заведения она короткая.
- Строки, которые пишет система, на странице записи — `RecordsInlineMixin`
  (иначе заведение их не видит).
- Новую настройку бота — в `BOT_SETTINGS_GROUPS` (`apps/partners/admin.py`).
- Все страницы — `BoteraModelAdmin` / `ScopedAdminMixin`, inline — из
  `unfold.admin`, у полей форм — виджеты Unfold. Шаблон меню — копия Unfold в
  `templates/unfold/helpers/` (при обновлении Unfold сверить).
- Счёт и быструю продажу заведение не удаляет (`allow_partner_delete`): счёт
  аннулируется с причиной, оплаченный — возвратом.
- Формы Admin не пишут деньги, остатки и состояние мимо сервиса владельца
  (`AGENTS.md`); остаток — только пересчётом (`count_stock`).
