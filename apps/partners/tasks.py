"""Background jobs of the partners module."""

from celery import shared_task

from apps.partners.networks import forget_old_message_points

FORGET_OLD_MESSAGE_POINTS_TASK = "partners.forget_old_message_points"


@shared_task(name=FORGET_OLD_MESSAGE_POINTS_TASK)
def forget_old_message_points_task() -> int:
    """A network bot's marks of which venue a message is for, past 90 days
    (PQ-047): a button that old acts for the guest's chosen venue."""

    return forget_old_message_points()
