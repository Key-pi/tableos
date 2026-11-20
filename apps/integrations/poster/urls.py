from django.urls import path

from apps.integrations.poster.views import oauth_callback

app_name = "poster"

urlpatterns = [
    path("oauth/callback/", oauth_callback, name="oauth-callback"),
]
