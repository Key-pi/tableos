"""Drop the cached owner's texts when one is saved or deleted (AA-047).

Signals rather than ``BotText.save``: Admin's «delete selected» and cascades
delete through querysets, which still send ``post_delete`` per row.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.partners.languages import forget_text_overrides
from apps.partners.models import BotText


@receiver(post_save, sender=BotText, dispatch_uid="partners.bot_text_saved")
@receiver(post_delete, sender=BotText, dispatch_uid="partners.bot_text_deleted")
def _forget_cached_texts(sender, instance: BotText, **_kwargs) -> None:
    forget_text_overrides(instance.partner_id)
