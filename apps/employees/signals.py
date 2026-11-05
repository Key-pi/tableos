"""Keep the network's owner and managers in step in every venue they run
(PQ-047): ``apps/employees/network_staff.py:sync_network_staff``."""

from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.employees.models import EmployeeProfile
from apps.employees.network_staff import networks_of_user, sync_network_staff
from apps.partners.models import Partner
from apps.users.models import AdminAccessProfile, User


@receiver(pre_save, sender=Partner, dispatch_uid="employees.partner_network_before")
def _remember_network(sender, instance: Partner, **_kwargs) -> None:
    instance._network_before = (
        Partner.objects.filter(pk=instance.pk).values_list("network_id", flat=True).first()
        if instance.pk
        else None
    )


@receiver(post_save, sender=Partner, dispatch_uid="employees.partner_network_after")
def _venue_moved(sender, instance: Partner, **_kwargs) -> None:
    before = getattr(instance, "_network_before", None)
    if before != instance.network_id:
        sync_network_staff(before)
        sync_network_staff(instance.network_id)


@receiver(post_save, sender=AdminAccessProfile, dispatch_uid="employees.access_saved")
@receiver(post_delete, sender=AdminAccessProfile, dispatch_uid="employees.access_deleted")
def _access_changed(sender, instance: AdminAccessProfile, **_kwargs) -> None:
    network_id = (
        Partner.objects.filter(id=instance.partner_id).values_list("network_id", flat=True).first()
    )
    sync_network_staff(network_id)


@receiver(
    m2m_changed, sender=AdminAccessProfile.extra_partners.through,
    dispatch_uid="employees.extra_venues_changed",
)
def _extra_venues_changed(sender, instance, action, **_kwargs) -> None:
    if action in {"post_add", "post_remove", "post_clear"} and isinstance(
        instance, AdminAccessProfile
    ):
        _access_changed(AdminAccessProfile, instance)


@receiver(post_save, sender=EmployeeProfile, dispatch_uid="employees.own_profile_saved")
def _own_profile_changed(sender, instance: EmployeeProfile, **_kwargs) -> None:
    if instance.mirrored_from_id is not None:
        return  # a copy saved by the sync itself
    for network_id in networks_of_user(instance.user):
        sync_network_staff(network_id)


@receiver(post_save, sender=User, dispatch_uid="employees.user_saved")
def _user_changed(sender, instance: User, **_kwargs) -> None:
    for network_id in networks_of_user(instance):
        sync_network_staff(network_id)
