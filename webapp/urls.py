from django.urls import path

from webapp import (
    kitchen_views,
    staff_billing,
    staff_reservations,
    staff_sales,
    staff_table_orders,
    staff_views,
    views,
)

app_name = "webapp"

urlpatterns = [
    # Stage 3: the kitchen/bar screen, without Telegram. Before "<bot>/".
    path("screen/", kitchen_views.kitchen_page, name="kitchen"),
    path("screen/api/state", kitchen_views.kitchen_state_view, name="kitchen_state"),
    path("screen/api/action", kitchen_views.kitchen_action_view, name="kitchen_action"),
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

# Stage 2: the staff screen (PQ-048).

urlpatterns += [
    path("<str:bot_hex>/staff/", staff_views.staff_page, name="staff"),
    path("<str:bot_hex>/api/staff/state", staff_views.staff_state_view, name="staff_state"),
    path("<str:bot_hex>/api/staff/action", staff_views.staff_action_view, name="staff_action"),
    path("<str:bot_hex>/api/staff/call", staff_views.staff_call_view, name="staff_call"),
]
urlpatterns += [
    path("<str:bot_hex>/api/staff/bills", staff_billing.bills_view, name="staff_bills"),
    path("<str:bot_hex>/api/staff/bill", staff_billing.bill_view, name="staff_bill"),
    path("<str:bot_hex>/api/staff/bill_action", staff_billing.bill_action_view,
         name="staff_bill_action"),
    path("<str:bot_hex>/api/staff/request_action", staff_billing.request_action_view,
         name="staff_request_action"),
    path("<str:bot_hex>/api/staff/tables", staff_billing.tables_view, name="staff_tables"),
    # PQ-052, step 2: an order to a table from the tablet.
    path("<str:bot_hex>/api/staff/table_order_menu", staff_table_orders.menu_view,
         name="staff_table_order_menu"),
    path("<str:bot_hex>/api/staff/table_order_preview", staff_table_orders.preview_view,
         name="staff_table_order_preview"),
    path("<str:bot_hex>/api/staff/table_order", staff_table_orders.order_view,
         name="staff_table_order"),
    path("<str:bot_hex>/api/staff/visit_action", staff_billing.visit_action_view,
         name="staff_visit_action"),
    # PQ-052, step 3: reservations.
    path("<str:bot_hex>/api/staff/reservations", staff_reservations.list_view,
         name="staff_reservations"),
    path("<str:bot_hex>/api/staff/reservation_save", staff_reservations.save_view,
         name="staff_reservation_save"),
    path("<str:bot_hex>/api/staff/reservation_action", staff_reservations.action_view,
         name="staff_reservation_action"),
]
urlpatterns += [
    path("<str:bot_hex>/api/staff/order", staff_views.staff_order_view, name="staff_order"),
    path("<str:bot_hex>/api/staff/bill_change", staff_billing.bill_change_view,
         name="staff_bill_change"),
    path("<str:bot_hex>/api/staff/refund", staff_billing.refund_view, name="staff_refund"),
    path("<str:bot_hex>/api/staff/refund_preview", staff_billing.refund_preview_view,
         name="staff_refund_preview"),
    path("<str:bot_hex>/api/staff/refund_do", staff_billing.refund_do_view,
         name="staff_refund_do"),
]
urlpatterns += [
    path("<str:bot_hex>/api/staff/sale_menu", staff_sales.sale_menu_view, name="staff_sale_menu"),
    path("<str:bot_hex>/api/staff/sale_preview", staff_sales.sale_preview_view,
         name="staff_sale_preview"),
    path("<str:bot_hex>/api/staff/sale_confirm", staff_sales.sale_confirm_view,
         name="staff_sale_confirm"),
    path("<str:bot_hex>/api/staff/report", staff_sales.report_view, name="staff_report"),
]
urlpatterns += [
    path("<str:bot_hex>/api/staff/invite_roles", staff_sales.invite_roles_view,
         name="staff_invite_roles"),
    path("<str:bot_hex>/api/staff/invite", staff_sales.invite_view, name="staff_invite"),
]
