from django.urls import path
from . import views

app_name = "payments"

urlpatterns = [
    # إنشاء الدفع
    path("pay/<uuid:package_id>/", views.start_payment, name="start_payment"),


    # العودة من Telr
    path("telr/success/", views.telr_success, name="telr_success"),
    path("telr/failed/", views.telr_failed, name="telr_failed"),
    path("telr/cancel/", views.telr_cancel, name="telr_cancel"),\
    path("telr/webhook/", views.telr_webhook, name="telr_webhook"),
    

]
