from dataclasses import dataclass

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


def load_active_bot_configs() -> list[BotRuntimeConfig]:
    active_instances = list(
        BotInstance.objects.filter(is_active=True).select_related("partner")
    )
    errors: list[str] = []
    seen_tokens: dict[str, str] = {}
    configs: list[BotRuntimeConfig] = []

    for instance in active_instances:
        if instance.partner.status != Partner.Status.ACTIVE:
            errors.append(
                f"@{instance.username}: partner `{instance.partner.slug}` is not active."
            )
            continue

        if instance.mode != BotInstance.Mode.POLLING:
            errors.append(
                f"@{instance.username}: webhook mode is not supported by `runbot`."
            )
            continue

        try:
            token = instance.token
        except Exception as exc:
            errors.append(
                f"@{instance.username}: token cannot be decrypted "
                f"({type(exc).__name__})."
            )
            continue

        if BotInstance.is_placeholder_token(token):
            errors.append(f"@{instance.username}: replace the seed placeholder token first.")
            continue

        if not BotInstance.looks_like_valid_token(token):
            errors.append(f"@{instance.username}: token format looks invalid.")
            continue

        duplicate_username = seen_tokens.get(token)
        if duplicate_username is not None:
            errors.append(
                f"@{instance.username}: token is duplicated with @{duplicate_username}."
            )
            continue
        seen_tokens[token] = instance.username

        configs.append(
            BotRuntimeConfig(
                partner_id=str(instance.partner_id),
                bot_instance_id=str(instance.id),
                username=instance.username,
                token=token,
            )
        )

    if errors:
        raise BotRuntimeConfigError("\n".join(errors))

    return configs


def register_runtime_configs(configs: list[BotRuntimeConfig]) -> None:
    global _ACTIVE_BOT_CONFIGS_BY_TOKEN
    _ACTIVE_BOT_CONFIGS_BY_TOKEN = {config.token: config for config in configs}


def get_runtime_config_by_token(bot_token: str) -> BotRuntimeConfig:
    try:
        return _ACTIVE_BOT_CONFIGS_BY_TOKEN[bot_token]
    except KeyError as exc:
        raise LookupError("Bot runtime configuration is not registered for this token.") from exc
