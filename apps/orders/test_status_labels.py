"""Order statuses are shown to guests and staff in Russian."""

from django.test import SimpleTestCase

from apps.orders.models import Order


class OrderStatusLabelTests(SimpleTestCase):
    def test_every_status_has_a_russian_label(self):
        self.assertEqual(
            dict(Order.Status.choices),
            {
                "new": "Новый",
                "accepted": "Принят",
                "preparing": "Готовится",
                "ready": "Готов",
                "delivering": "В пути",
                "completed": "Завершён",
                "canceled": "Отменён",
            },
        )
