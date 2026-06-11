from django.db import models


class OrderStatus(models.TextChoices):
    NEW = "new", "New"
    ACCEPTED = "accepted", "Accepted"
    PREPARING = "preparing", "Preparing"
    READY = "ready", "Ready"
    DELIVERING = "delivering", "Delivering"
    COMPLETED = "completed", "Completed"
    CANCELED = "canceled", "Canceled"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    TERMINAL = "terminal", "Terminal"
