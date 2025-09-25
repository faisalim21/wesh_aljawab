# payments/urls.py
from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    path('', views.payments_home, name='home'),

    # شراء
    path('purchase/<uuid:package_id>/', views.purchase_package, name='purchase_package'),
    # alias اختياري للتوافق
    path('purchase/<uuid:package_id>/', views.purchase_package, name='purchase'),

    # صفحات عامة
    path('success/', views.payment_success, name='success'),
    path('cancel/', views.payment_cancel, name='cancel'),
    path('history/', views.transaction_history, name='history'),
    path('invoice/<uuid:transaction_id>/', views.invoice_view, name='invoice'),
    path('rajhi/checkout/', views.rajhi_checkout, name='rajhi_checkout'),
    path("rajhi/direct-init/", views.rajhi_direct_init, name="rajhi_direct_init"),

    # اختبارات تكوين الراجحي
    path('rajhi-test/', views.rajhi_test, name='rajhi_test'),
    path('rajhi/callback/success/', views.rajhi_callback_success, name='rajhi_callback_success'),
    path('rajhi/callback/fail/', views.rajhi_callback_fail, name='rajhi_callback_fail'),
]
