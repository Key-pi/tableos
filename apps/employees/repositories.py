from apps.employees.models import EmployeeProfile


class EmployeeRepository:
    @staticmethod
    def active_for_partner(partner_id):
        return EmployeeProfile.objects.filter(
            partner_id=partner_id,
            is_active=True,
        ).select_related("user", "telegram_account")
