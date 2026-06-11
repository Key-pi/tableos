from django.db import models

from core.database.models import PartnerBoundModel


class NotificationPreference(PartnerBoundModel):
    """Per-guest notification opt-in settings within a specific partner."""

    guest = models.OneToOneField(
        "users.GuestProfile",
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    order_updates_enabled = models.BooleanField(default=True)
    bonus_updates_enabled = models.BooleanField(default=True)
    marketing_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "guest_id"]

    def __str__(self) -> str:
        return f"{self.partner.name} / preferences / {self.guest_id}"


class StaffNotification(PartnerBoundModel):
    """Internal notification delivered to partner staff about operational events."""

    class Category(models.TextChoices):
        ORDER_CREATED = "order_created", "Order created"
        ORDER_STATUS_CHANGED = "order_status_changed", "Order status changed"
        GUEST_CALL = "guest_call", "Guest call"
        BILLING_REQUEST = "billing_request", "Billing request"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    employee = models.ForeignKey(
        "employees.EmployeeProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_notifications",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="staff_notifications",
    )
    category = models.CharField(max_length=32, choices=Category.choices)
    title = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    delivery_status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.category} / {self.title}"


class BroadcastCampaign(PartnerBoundModel):
    """Partner marketing or announcement message scheduled for mass delivery."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    name = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    delivered_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    delivery_started_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.partner.name} / {self.name}"
