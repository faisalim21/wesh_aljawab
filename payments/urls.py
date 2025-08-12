from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    path('', views.payments_home, name='home'),
    path('purchase/<uuid:package_id>/', views.purchase_package, name='purchase'),
    path('success/', views.payment_success, name='success'),
    path('cancel/', views.payment_cancel, name='cancel'),
    path('history/', views.transaction_history, name='history'),
    path('invoice/<uuid:transaction_id>/', views.invoice_view, name='invoice'),
]
