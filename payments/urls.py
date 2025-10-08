# payments/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("create/<uuid:package_id>/", views.create_payment, name="create_payment"),
    path("return/", views.payment_return, name="payment_return"),
    path("webhook/", views.payment_webhook, name="payment_webhook"),
]
