"""Photos of menu items uploaded in Admin (PQ-048)."""

import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.menu.models import MenuCategory, MenuItem
from apps.partners.models import Partner
from apps.users.constants import AdminAccessPreset
from apps.users.models import AdminAccessProfile, User


def png(name="photo.png", size=(40, 30)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class MenuPhotoTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.partner = Partner.objects.create(
            name="Кава", slug="kava", status=Partner.Status.ACTIVE
        )
        self.category = MenuCategory.objects.create(partner=self.partner, name="Кава")
        owner = User.objects.create_user(
            username="kava_owner", password="pass12345", partner=self.partner,
            role=User.Role.OWNER, is_staff=True,
        )
        AdminAccessProfile.objects.create(
            user=owner, partner=self.partner, preset=AdminAccessPreset.OWNER,
            is_partner_owner=True, can_access_admin=True,
        )
        self.client.force_login(owner)

    def add(self, photo, **extra):
        data = {
            "category": str(self.category.id), "name": "Лате", "description": "",
            "price": "75.00", "image_url": "", "item_type": "internal",
            "is_available": "on", "available_at_table": "on", "available_for_pickup": "on",
            "available_for_delivery": "on", "available_for_shipping": "on",
            "sort_order": "100", "image": photo, **extra,
        }
        return self.client.post(reverse("admin:menu_menuitem_add"), data)

    def test_the_owner_uploads_a_photo_and_it_is_served(self):
        response = self.add(png())
        self.assertEqual(response.status_code, 302)
        item = MenuItem.objects.get()
        self.assertTrue(item.image.name.startswith(f"menu/{self.partner.id.hex}/"))
        self.assertTrue(item.photo_url.startswith("/media/menu/"))
        served = self.client.get(item.photo_url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/png")
        self.assertIn("max-age", served["Cache-Control"])

    def test_not_an_image_or_too_big_is_refused(self):
        fake = SimpleUploadedFile("menu.png", b"<script>alert(1)</script>", "image/png")
        response = self.add(fake)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MenuItem.objects.exists())
        with self.settings(MENU_PHOTO_MAX_BYTES=100):
            response = self.add(png())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MenuItem.objects.exists())

    def test_the_link_stays_when_there_is_no_upload(self):
        item = MenuItem.objects.create(
            partner=self.partner, category=self.category, name="Чай", price="40.00",
            image_url="https://example.com/tea.jpg",
        )
        self.assertEqual(item.photo_url, "https://example.com/tea.jpg")
        item.image_url = ""
        self.assertEqual(item.photo_url, "")

    def test_nothing_outside_the_media_folder_is_served(self):
        for path in ("/media/../manage.py", "/media/%2e%2e/manage.py", "/media/menu/"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
