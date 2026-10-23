from django.test import TestCase, override_settings


class DemoHomeTests(TestCase):
    @override_settings(DEBUG=False)
    def test_public_root_discloses_no_platform_counts_in_production(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Заведений".encode(), response.content)
        self.assertNotIn("Заказов".encode(), response.content)

    @override_settings(DEBUG=True)
    def test_debug_root_shows_the_demo_page(self):
        response = self.client.get("/")
        self.assertIn("Заведений".encode(), response.content)
        self.assertIn("Botera Shop".encode(), response.content)
