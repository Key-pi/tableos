from django.apps import AppConfig

from core.i18n import gettext_lazy as _lazy


class PosterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations.poster"
    label = "poster"
    verbose_name = _lazy("Интеграция с Poster")

    def ready(self):
        # A remote order goes to the Poster register after its commit (step 5).
        from apps.billing.signals import remote_order_placed
        from apps.integrations.poster.orders import on_remote_order_placed

        remote_order_placed.connect(on_remote_order_placed,
                                    dispatch_uid="poster.queue_remote_order")
