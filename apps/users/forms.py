from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from unfold.forms import UnfoldReadOnlyPasswordHashWidget
from unfold.widgets import UnfoldAdminPasswordWidget, UnfoldAdminRadioSelectWidget

from apps.users.models import User


class AdminUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "partner",
            "role",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Unfold's widgets (admin step 1); Django's own texts stay translated.
        for name in ("password1", "password2"):
            self.fields[name].widget = UnfoldAdminPasswordWidget(
                attrs={"autocomplete": "new-password"}
            )
        if "usable_password" in self.fields:
            self.fields["usable_password"].widget = UnfoldAdminRadioSelectWidget(
                choices=self.fields["usable_password"].choices
            )


class AdminUserChangeForm(UserChangeForm):
    password = UserChangeForm.base_fields["password"]

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "partner",
            "role",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "password" in self.fields:
            self.fields["password"].widget = UnfoldReadOnlyPasswordHashWidget()
