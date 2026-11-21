"""What billing tells other parts after a commit (PQ-058).

``remote_order_placed`` — a guest placed a remote order (pickup, delivery,
shipping; ``apps/billing/remote_orders.py:place_remote_order``); sent once
the order is committed, with ``order_id``. The Poster integration listens
(``apps/integrations/poster/apps.py``) to send the order to the register.
Billing knows nothing of who listens, and a listener that fails never fails
the order: ``send_robust`` catches it and Django logs it (``django.dispatch``).
"""

from django.dispatch import Signal

remote_order_placed = Signal()


def announce_remote_order(order_id) -> None:
    from apps.orders.models import Order

    remote_order_placed.send_robust(sender=Order, order_id=order_id)
