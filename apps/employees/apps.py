from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class EmployeesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.employees"
    label = "employees"
    verbose_name = _lazy("Сотрудники")
