from django.urls import path
from . import views

app_name = "payments"

urlpatterns = [
    # إنشاء جلسة الدفع
    path("create/<uuid:package_id>/", views.create_payment, name="create_payment"),

    # العودة من تلر بعد الدفع
    path("telr/success/", views.telr_success, name="telr_success"),
    path("telr/fail/", views.telr_fail, name="telr_fail"),
    path("telr/cancel/", views.telr_cancel, name="telr_cancel"),
]
