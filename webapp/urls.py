from django.urls import path

from webapp import views

app_name = "webapp"

urlpatterns = [
    path("<str:bot_hex>/", views.guest_page, name="guest"),
    path("<str:bot_hex>/api/state", views.state_view, name="state"),
    path("<str:bot_hex>/api/point", views.point_view, name="point"),
    path("<str:bot_hex>/api/menu", views.menu_view, name="menu"),
    path("<str:bot_hex>/api/channel", views.channel_view, name="channel"),
    path("<str:bot_hex>/api/cart", views.cart_view, name="cart"),
    path("<str:bot_hex>/api/checkout", views.checkout_view, name="checkout"),
    path("<str:bot_hex>/api/details", views.details_view, name="details"),
    path("<str:bot_hex>/api/place", views.place_view, name="place"),
]
