from django import forms

from apps.partners.bot_texts import GROUP_LABELS, editable_texts
from apps.partners.models import BotInstance, BotText, text_key
from core.i18n import _, gettext_in, placeholders


class BotInstanceAdminForm(forms.ModelForm):
    username = forms.CharField(
        label="Имя бота (без @)",
        required=True,
        help_text=(
            "Имя бота в Telegram без @, например smoke_lounge_bot. "
            "По нему строятся ссылки для QR-кодов столов."
        ),
    )
    raw_token = forms.CharField(
        label="Токен бота",
        required=False,
        widget=forms.PasswordInput(render_value=True),
        help_text="Вставьте токен от @BotFather. Он хранится зашифрованным.",
    )

    class Meta:
        model = BotInstance
        fields = (
            "partner",
            "display_name",
            "username",
            "raw_token",
            "mode",
            "webhook_url",
            "is_active",
        )

    def clean_username(self) -> str:
        username = BotInstance.normalize_username(self.cleaned_data["username"])
        if not username:
            raise forms.ValidationError("Укажите имя бота.")
        if not BotInstance.looks_like_valid_username(username):
            raise forms.ValidationError(
                "Имя бота Telegram без @, например smoke_lounge_bot."
            )
        return username

    def clean(self):
        cleaned_data = super().clean()
        raw_token = cleaned_data.get("raw_token", "").strip()
        is_active = cleaned_data.get("is_active", False)
        mode = cleaned_data.get("mode")

        if raw_token and not BotInstance.looks_like_valid_token(raw_token):
            self.add_error(
                "raw_token",
                "Токен не похож на настоящий. Вставьте токен от @BotFather целиком.",
            )

        if mode == BotInstance.Mode.WEBHOOK and is_active:
            self.add_error(
                "mode",
                "Режим webhook пока не поддерживается — выберите polling.",
            )

        if not self.instance.pk and not raw_token:
            self.add_error("raw_token", "Bot token is required when creating a bot instance.")

        if is_active and not raw_token and not self.instance.has_usable_token:
            self.add_error(
                "raw_token",
                "A real bot token is required before you can activate this bot instance.",
            )
        return cleaned_data

    def save(self, commit: bool = True) -> BotInstance:
        instance = super().save(commit=False)
        raw_token = self.cleaned_data.get("raw_token", "").strip()
        if raw_token:
            instance.set_token(raw_token)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class BotTextForm(forms.ModelForm):
    """The owner's own wording of one catalog text in one language (PQ-040)."""

    scope_partner_id = None  # set by the admin for a venue's own staff

    msgid = forms.ChoiceField(label="Текст в каталоге")

    class Meta:
        model = BotText
        fields = ("partner", "language", "msgid", "text")
        widgets = {"text": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        texts = editable_texts()
        current = self.instance.msgid if self.instance.pk else None
        choices = [
            (msgid, f"{_(GROUP_LABELS[group])} · {_short(msgid)}")
            for msgid, group in sorted(texts.items(), key=lambda item: (item[1], item[0]))
        ]
        if current and current not in texts:
            choices.insert(0, (current, f"(нет в каталоге) {_short(current)}"))
        self.fields["msgid"].choices = choices
        msgid = self.initial.get("msgid") or current
        if msgid:
            names = sorted(placeholders(msgid))
            hint = "Подстановки: " + ", ".join(f"{{{name}}}" for name in names) if names else ""
            translations = " · ".join(
                f"{code}: {gettext_in(code, msgid)}" for code in ("uk", "ru", "en")
            )
            self.fields["text"].help_text = " ".join(filter(None, [hint, translations]))

    def _post_clean(self):
        if self.scope_partner_id is not None:
            self.instance.partner_id = self.scope_partner_id
        super()._post_clean()

    def clean(self):
        cleaned = super().clean()
        partner_id = self.scope_partner_id or getattr(cleaned.get("partner"), "id", None)
        language, msgid = cleaned.get("language"), cleaned.get("msgid")
        if partner_id and language and msgid:
            duplicate = BotText.objects.filter(
                partner_id=partner_id, language=language, key=text_key(msgid)
            ).exclude(id=self.instance.id)
            if duplicate.exists():
                raise forms.ValidationError(
                    "Для этого текста и языка уже есть свой вариант — измените его."
                )
        return cleaned


def _short(text: str, limit: int = 90) -> str:
    flat = " ⏎ ".join(line for line in text.splitlines() if line)
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
