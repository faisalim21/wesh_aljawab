
from django.urls import path
from . import views

app_name = "payments"

urlpatterns = [
    # صفحات الدفع العادية
    path("", views.payments_home, name="home"),
    path("purchase/<uuid:package_id>/", views.purchase_package, name="purchase"),
    path("success/", views.payment_success, name="success"),
    path("cancel/", views.payment_cancel, name="cancel"),
    path("history/", views.transaction_history, name="history"),
    path("invoice/<int:transaction_id>/", views.invoice_view, name="invoice"),

    # 🔍 صفحات الراجحي
    path("rajhi/test/", views.rajhi_test, name="rajhi_test"),              # ← أضف هذا
    path("rajhi/direct-init/", views.rajhi_direct_init, name="rajhi_direct_init"),
    path("rajhi/callback/success/", views.rajhi_callback_success, name="rajhi_callback_success"),
    path("rajhi/callback/fail/", views.rajhi_callback_fail, name="rajhi_callback_fail"),
    path("rajhi/checkout/", views.rajhi_checkout, name="rajhi_checkout"),
]
