from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from apps.bonuses.admin import WalkInSaleAdmin
from apps.bonuses.models import BonusProgram, WalkInSale, WalkInSaleItem
from apps.bonuses.services import (
    preview_walk_in_sale_by_customer_code,
    register_walk_in_sale,
    register_walk_in_sale_by_customer_code,
    register_walk_in_sale_from_menu_items_by_customer_code,
)
from apps.menu.models import MenuCategory, MenuItem
from apps.partners.models import Partner
from apps.users.constants import AdminSection
from apps.users.models import (
    AdminAccessProfile,
    AdminSectionPermission,
    GuestProfile,
    TelegramAccount,
    User,
)


class WalkInSaleServiceTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Walk In Venue",
            slug="walk-in-venue",
            status=Partner.Status.ACTIVE,
        )
        telegram_account = TelegramAccount.objects.create(
            telegram_id=12345001,
            username="walkin_guest",
        )
        self.guest = GuestProfile.objects.create(
            partner=self.partner,
            telegram_account=telegram_account,
        )
        self.program = BonusProgram.objects.create(
            partner=self.partner,
            name="Walk-in cashback",
            trigger_event=BonusProgram.TriggerEvent.MANUAL_PURCHASE,
            program_type=BonusProgram.ProgramType.CASHBACK,
            percent="5.00",
            is_active=True,
        )
        category = MenuCategory.objects.create(
            partner=self.partner,
            name="Bar",
            sort_order=10,
        )
        self.coffee = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            name="Coffee",
            price="90.00",
            sort_order=10,
        )
        self.tea = MenuItem.objects.create(
            partner=self.partner,
            category=category,
            name="Tea",
            price="80.00",
            sort_order=20,
        )

    def test_register_walk_in_sale_with_customer_code_applies_bonus(self):
        sale = register_walk_in_sale(
            partner_id=self.partner.id,
            guest=self.guest,
            customer_code_snapshot=self.guest.customer_code,
            amount="500.00",
            comment="Bar purchase",
        )

        self.guest.refresh_from_db()
        self.assertEqual(sale.customer_code_snapshot, self.guest.customer_code)
        self.assertEqual(sale.bonus_awarded_amount, Decimal("25.00"))
        self.assertEqual(self.guest.loyalty_balance, Decimal("25.00"))

    def test_register_walk_in_sale_without_customer_code_keeps_sale_anonymous(self):
        sale = register_walk_in_sale(
            partner_id=self.partner.id,
            amount="320.00",
            comment="Anonymous bar purchase",
        )

        self.guest.refresh_from_db()
        self.assertIsNone(sale.guest)
        self.assertEqual(sale.customer_code_snapshot, "")
        self.assertEqual(sale.bonus_awarded_amount, Decimal("0.00"))
        self.assertEqual(self.guest.loyalty_balance, Decimal("0.00"))

    def test_register_walk_in_sale_by_customer_code_resolves_guest(self):
        sale = register_walk_in_sale_by_customer_code(
            partner_id=self.partner.id,
            customer_code=self.guest.customer_code.lower(),
            amount="200.00",
            comment="Cash desk sale",
        )

        self.assertEqual(sale.guest_id, self.guest.id)
        self.assertEqual(sale.customer_code_snapshot, self.guest.customer_code)

    def test_preview_walk_in_sale_by_customer_code_returns_total_and_bonus(self):
        preview = preview_walk_in_sale_by_customer_code(
            partner_id=self.partner.id,
            customer_code=self.guest.customer_code,
            items=[
                {"menu_item_id": self.coffee.id, "quantity": 2},
                {"menu_item_id": self.tea.id, "quantity": 1},
            ],
        )

        self.assertEqual(preview.guest.id, self.guest.id)
        self.assertEqual(preview.total_amount, Decimal("260.00"))
        self.assertEqual(preview.projected_bonus_amount, Decimal("13.00"))

    def test_register_walk_in_sale_from_menu_items_by_customer_code_creates_line_items(self):
        sale = register_walk_in_sale_from_menu_items_by_customer_code(
            partner_id=self.partner.id,
            customer_code=self.guest.customer_code,
            items=[
                {"menu_item_id": self.coffee.id, "quantity": 2},
                {"menu_item_id": self.tea.id, "quantity": 1},
            ],
            comment="Quick bar sale",
        )

        self.guest.refresh_from_db()
        self.assertEqual(sale.amount, Decimal("260.00"))
        self.assertEqual(sale.bonus_awarded_amount, Decimal("13.00"))
        self.assertEqual(sale.items.count(), 2)
        self.assertTrue(
            WalkInSaleItem.objects.filter(
                sale=sale,
                item_name="Coffee",
                quantity=2,
            ).exists()
        )

    def test_preview_walk_in_sale_with_empty_code_is_anonymous(self):
        preview = preview_walk_in_sale_by_customer_code(
            partner_id=self.partner.id,
            customer_code="",
            items=[
                {"menu_item_id": self.coffee.id, "quantity": 2},
            ],
        )

        self.assertIsNone(preview.guest)
        self.assertEqual(preview.total_amount, Decimal("180.00"))
        self.assertEqual(preview.projected_bonus_amount, Decimal("0.00"))

    def test_register_walk_in_sale_from_menu_items_anonymous(self):
        sale = register_walk_in_sale_from_menu_items_by_customer_code(
            partner_id=self.partner.id,
            customer_code="",
            items=[
                {"menu_item_id": self.coffee.id, "quantity": 1},
                {"menu_item_id": self.tea.id, "quantity": 1},
            ],
            comment="Аноним у бара",
        )

        self.assertIsNone(sale.guest)
        self.assertEqual(sale.customer_code_snapshot, "")
        self.assertEqual(sale.loyalty_label, "Аноним")
        self.assertEqual(sale.amount, Decimal("170.00"))
        self.assertEqual(sale.bonus_awarded_amount, Decimal("0.00"))
        self.assertEqual(sale.items.count(), 2)

    def test_register_walk_in_sale_by_code_rejects_unknown_code(self):
        from apps.bonuses.services import BonusServiceError

        with self.assertRaises(BonusServiceError):
            register_walk_in_sale_by_customer_code(
                partner_id=self.partner.id,
                customer_code="NOPE99",
                amount="100.00",
            )

    def test_register_walk_in_sale_from_menu_items_preserves_comment(self):
        sale = register_walk_in_sale_from_menu_items_by_customer_code(
            partner_id=self.partner.id,
            customer_code=self.guest.customer_code,
            items=[
                {"menu_item_id": self.coffee.id, "quantity": 1},
            ],
            comment="Без сиропа, с собой",
        )

        self.assertEqual(sale.comment, "Без сиропа, с собой")


class WalkInSaleAdminPermissionTests(TestCase):
    def setUp(self):
        self.partner = Partner.objects.create(
            name="Admin QA",
            slug="admin-qa",
            status=Partner.Status.ACTIVE,
        )
        self.user = User.objects.create_user(
            username="bonus_manager",
            password="tableos12345",
            partner=self.partner,
            role=User.Role.MANAGER,
            is_staff=True,
        )
        profile = AdminAccessProfile.objects.create(
            user=self.user,
            partner=self.partner,
            can_access_admin=True,
            is_active=True,
        )
        AdminSectionPermission.objects.create(
            profile=profile,
            section=AdminSection.BONUSES,
            can_view=True,
            can_add=True,
            can_change=True,
            can_delete=False,
            can_manage_sensitive=False,
        )
        self.sale = register_walk_in_sale(
            partner_id=self.partner.id,
            amount="120.00",
            comment="Immutable sale",
        )
        self.admin = WalkInSaleAdmin(WalkInSale, AdminSite())

    def test_existing_walk_in_sale_is_view_only_in_admin(self):
        request = type("Request", (), {"user": self.user})()

        self.assertFalse(self.admin.has_change_permission(request, self.sale))
        self.assertTrue(self.admin.has_view_permission(request, self.sale))
