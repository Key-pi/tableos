"""Commands of the partners module."""

from __future__ import annotations

from django.utils import timezone

from apps.partners.models import BotInstance


def record_bot_runtime_states(states: dict[str, str]) -> int:
    """Store what the bot service found for each bot (PQ-041): ``{bot
    instance id: BotInstance.RuntimeState}``. Writes only bots whose state
    changed, so the check every minute does not rewrite every row.
    Returns how many bots were updated."""

    updated = 0
    now = timezone.now()
    for state in set(states.values()):
        ids = [instance_id for instance_id, value in states.items() if value == state]
        updated += (
            BotInstance.objects.filter(id__in=ids)
            .exclude(runtime_state=state)
            .update(runtime_state=state, runtime_checked_at=now)
        )
    return updated
