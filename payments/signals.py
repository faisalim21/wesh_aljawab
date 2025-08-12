# payments/signals.py
from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django.apps import apps
from .models import PaymentMethod, FakePaymentGateway

@receiver(post_migrate)
def ensure_default_payment_setup(sender, **kwargs):
    """
    يضمن وجود وسيلة دفع افتراضية وبوابة وهمية (مفعّلة) بعد أي migrate
    ويعمل فقط عند انتهاء ترحيل تطبيق payments.
    """
    try:
        # اشتغل فقط لتطبيق payments
        if not sender or getattr(sender, "name", "") != "payments":
            return

        # وسيلة دفع افتراضية (بطاقة)
        PaymentMethod.objects.get_or_create(
            name="CARD",
            defaults={
                "name_ar": "بطاقة",
                "is_active": True,
                "processing_fee": 0.00,
            },
        )

        # تأكد من وجود بوابة وهمية مفعّلة واحدة على الأقل
        if not FakePaymentGateway.objects.filter(is_active=True).exists():
            FakePaymentGateway.objects.create(
                name="بوابة وهمية",
                success_rate=95,
                processing_delay=2,
                is_active=True,
            )
    except Exception:
        # ما نفشل الترحيل لو صار شيء
        pass
