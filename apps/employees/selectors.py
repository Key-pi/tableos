from apps.employees.models import EmployeeProfile


def employee_directory(partner_id):
    return EmployeeProfile.objects.filter(partner_id=partner_id).select_related(
        "user",
        "telegram_account",
    )


def get_staff_employee_by_telegram(partner_id, telegram_id: int) -> EmployeeProfile:
    return EmployeeProfile.objects.select_related("user", "telegram_account").get(
        partner_id=partner_id,
        telegram_account__telegram_id=telegram_id,
        is_active=True,
    )
