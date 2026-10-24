from django import forms

from apps.partners.models import BotInstance


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
