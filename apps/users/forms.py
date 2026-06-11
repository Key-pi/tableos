from django.contrib.auth.forms import UserChangeForm, UserCreationForm

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

