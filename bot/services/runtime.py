import uuid
from dataclasses import dataclass, field

from apps.partners.models import BotInstance, Partner


class BotRuntimeConfigError(Exception):
    """Raised when active bot configuration is unsafe to start in polling mode."""


@dataclass(slots=True)
class BotRuntimeConfig:
    partner_id: str
    bot_instance_id: str
    username: str
    token: str


_ACTIVE_BOT_CONFIGS_BY_TOKEN: dict[str, BotRuntimeConfig] = {}


State = BotInstance.RuntimeState


@dataclass(slots=True)
class BotScan:
    """Every active bot checked: the runnable ones and why the rest are not."""

    configs: list[BotRuntimeConfig] = field(default_factory=list)
    # bot instance id -> the state it runs in or why it does not
    states: dict[str, str] = field(default_factory=dict)
    # bot instance id -> human-readable reason for the server log
    problems: dict[str, str] = field(default_factory=dict)


def scan_bot_configs() -> BotScan:
    """Check every active bot on its own (PQ-041): a broken one is skipped
    with its reason, it never stops the others.

    A bot of a suspended partner keeps running: it answers that the work is
    suspended (PQ-013). Of two bots with one token the first one runs.
    """

    scan = BotScan()
    seen_tokens: dict[str, str] = {}
    instances = (
        BotInstance.objects.filter(is_active=True)
        .select_related("partner")
        .order_by("created_at", "id")
    )
    for instance in instances:
        instance_id = str(instance.id)

        def skip(state: str, reason: str, *, instance=instance, instance_id=instance_id) -> None:
            scan.states[instance_id] = state
            scan.problems[instance_id] = f"@{instance.username}: {reason}"

        if instance.mode != BotInstance.Mode.POLLING:
            skip(State.WEBHOOK, "webhook mode is not supported by `runbot`.")
            continue
        try:
            token = instance.token
        except Exception as exc:  # noqa: BLE001 - any decryption failure is the same problem
            skip(State.UNREADABLE_TOKEN, f"token cannot be decrypted ({type(exc).__name__}).")
            continue
        if BotInstance.is_placeholder_token(token):
            skip(State.PLACEHOLDER_TOKEN, "replace the seed placeholder token first.")
            continue
        if not BotInstance.looks_like_valid_token(token):
            skip(State.INVALID_TOKEN, "token format looks invalid.")
            continue
        duplicate_username = seen_tokens.get(token)
        if duplicate_username is not None:
            skip(State.DUPLICATE_TOKEN, f"token is duplicated with @{duplicate_username}.")
            continue
        seen_tokens[token] = instance.username
        scan.configs.append(
            BotRuntimeConfig(
                partner_id=str(instance.partner_id),
                bot_instance_id=instance_id,
                username=instance.username,
                token=token,
            )
        )
        scan.states[instance_id] = (
            State.RUNNING
            if instance.partner.status == Partner.Status.ACTIVE
            else State.RUNNING_SUSPENDED
        )
    return scan


def load_active_bot_configs() -> list[BotRuntimeConfig]:
    """All active bots, or an error if any of them cannot run.

    The strict check for tools that need every bot (the load-test stand);
    the bot service itself runs through ``scan_bot_configs``.
    """

    scan = scan_bot_configs()
    errors = list(scan.problems.values())
    errors.extend(
        f"@{config.username}: partner is not active."
        for config in scan.configs
        if scan.states[config.bot_instance_id] == State.RUNNING_SUSPENDED
    )
    if errors:
        raise BotRuntimeConfigError("\n".join(errors))
    return scan.configs


def register_runtime_configs(configs: list[BotRuntimeConfig]) -> None:
    global _ACTIVE_BOT_CONFIGS_BY_TOKEN
    _ACTIVE_BOT_CONFIGS_BY_TOKEN = {config.token: config for config in configs}


def get_runtime_config_by_token(bot_token: str) -> BotRuntimeConfig:
    try:
        return _ACTIVE_BOT_CONFIGS_BY_TOKEN[bot_token]
    except KeyError as exc:
        raise LookupError("Bot runtime configuration is not registered for this token.") from exc


def configs_for_shard(
    configs: list[BotRuntimeConfig], *, index: int, count: int
) -> list[BotRuntimeConfig]:
    """The bots one of ``count`` bot processes polls (``0 <= index < count``).

    A bot belongs to the shard ``bot_instance_id % count``: every process
    computes the same split on its own, a bot never lands in two processes
    (Telegram allows one poller per token), and adding or removing other bots
    does not move it.
    """

    if count <= 1:
        return list(configs)
    return [
        config
        for config in configs
        if uuid.UUID(config.bot_instance_id).int % count == index
    ]
