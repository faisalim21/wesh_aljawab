from django.urls import path
from . import views

app_name = "payments"

urlpatterns = [
    path("create/<uuid:package_id>/", views.create_payment, name="create_payment"),
    path("success/", views.payment_success, name="success"),
    path("failure/", views.payment_failure, name="failure"),
]
