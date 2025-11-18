import uuid
import logging
import requests
from decimal import Decimal

from django.conf import settings
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt

from django.contrib.auth.decorators import login_required

from games.models import GamePackage, UserPurchase
from payments.models import Transaction, PaymentMethod

logger = logging.getLogger(__name__)


# ============================================================
#   إنشاء جلسة دفع Telr (Hosted Payment Page)
# ============================================================
@login_required
@transaction.atomic
def create_payment(request, package_id):

    # 1) الحصول على الحزمة
    package = get_object_or_404(GamePackage, id=package_id)

    # 2) هل لديه شراء نشط؟
    existing = UserPurchase.objects.filter(
        user=request.user,
        package=package,
        expires_at__gt=timezone.now(),
        is_completed=False
    ).first()

    if existing:
        messages.info(request, "لديك شراء نشط لهذه الحزمة.")
        return redirect("games:letters_home")

    # 3) إنشاء Transaction داخل النظام قبل إرسال الطلب لـ Telr
    transaction_obj = Transaction.objects.create(
        user=request.user,
        package=package,
        amount=package.effective_price,
        currency="SAR",
        status="pending"
    )

    # 4) تجهيز البيانات لإرسالها لـ Telr
    payload = {
        "store": settings.TELR_STORE_ID,
        "authkey": settings.TELR_AUTH_KEY,
        "test": "1" if settings.TELR_TEST_MODE else "0",

        "order": {
            "cartid": str(transaction_obj.id),                              # رقم العملية عندك
            "amount": str(package.effective_price),
            "currency": "SAR",
            "description": f"Purchase package {package.package_number}"
        },

        "customer": {
            "email": request.user.email or "no-email@placeholder.com",
            "name": request.user.username
        },

        "return": {
            "authorised": settings.TELR_RETURN_SUCCESS,
            "declined": settings.TELR_RETURN_FAIL,
            "cancelled": settings.TELR_RETURN_CANCEL,
        }
    }

    # 5) طلب إنشاء جلسة Telr
    try:
        response = requests.post(
            "https://secure.telr.com/gateway/order.json",
            json=payload,
            timeout=10
        )
        data = response.json()

    except Exception as e:
        logger.error(f"Telr request failed: {e}")
        messages.error(request, "حدث خطأ أثناء الاتصال ببوابة الدفع.")
        return redirect("games:home")

    # 6) التحقق من الرد
    if "order" not in data or "url" not in data["order"]:
        messages.error(request, "تعذّر إنشاء جلسة دفع. حاول لاحقًا.")
        transaction_obj.status = "failed"
        transaction_obj.failure_reason = str(data)
        transaction_obj.save()
        return redirect("games:home")

    telr_url = data["order"]["url"]

    # 7) تحديث المعاملة
    transaction_obj.status = "processing"
    transaction_obj.gateway_transaction_id = data["order"]["ref"]
    transaction_obj.save()

    # 8) إعادة التوجيه إلى صفحة Telr
    return redirect(telr_url)



# ============================================================
#   العودة من Telr (نجاح الدفع)
# ============================================================
@csrf_exempt
def telr_success(request):
    ref = request.GET.get("ref")

    # 1) العثور على المعاملة
    trans = Transaction.objects.filter(gateway_transaction_id=ref).first()
    if not trans:
        messages.error(request, "معاملة غير معروفة.")
        return redirect("games:home")

    if trans.status == "completed":
        messages.success(request, "تمت العملية بنجاح سابقًا.")
        return redirect("games:home")

    # 2) تحديث الحالة
    trans.status = "completed"
    trans.completed_at = timezone.now()
    trans.save()

    # 3) إنشاء شراء جديد صالح لمدة 72 ساعة
    expiry_time = timezone.now() + timezone.timedelta(hours=72)

    UserPurchase.objects.create(
        user=trans.user,
        package=trans.package,
        expires_at=expiry_time
    )

    messages.success(request, "🎉 تمت عملية الدفع بنجاح! تم تفعيل الحزمة.")
    return redirect("games:home")



# ============================================================
#   العودة من Telr (رفض الدفع)
# ============================================================
@csrf_exempt
def telr_fail(request):
    ref = request.GET.get("ref")

    trans = Transaction.objects.filter(gateway_transaction_id=ref).first()
    if trans:
        trans.status = "failed"
        trans.failure_reason = "Payment declined"
        trans.save()

    messages.error(request, "عملية الدفع رُفضت.")
    return redirect("games:home")



# ============================================================
#   العودة من Telr (إلغاء الدفع)
# ============================================================
@csrf_exempt
def telr_cancel(request):
    ref = request.GET.get("ref")

    trans = Transaction.objects.filter(gateway_transaction_id=ref).first()
    if trans:
        trans.status = "cancelled"
        trans.save()

    messages.warning(request, "تم إلغاء عملية الدفع.")
    return redirect("games:home")
