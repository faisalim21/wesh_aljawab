import requests
import json
from django.shortcuts import redirect, get_object_or_404, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from games.models import GamePackage, UserPurchase
from .telr import generate_telr_url
from .models import TelrTransaction


# ============================
#   إنشاء عملية الدفع
# ============================

@login_required
def start_payment(request, package_id):
    """
    إنشاء عملية دفع وتوجيه المستخدم لصفحة Telr
    """

    package = get_object_or_404(GamePackage, id=package_id, is_active=True)

    if package.is_free:
        return render(request, "payments/error.html", {
            "message": "هذه الحزمة مجانية ولا تحتاج إلى شراء."
        })

    # التحقق من وجود شراء نشط لنفس الحزمة
    purchase = UserPurchase.objects.filter(
        user=request.user,
        package=package,
        is_completed=False
    ).first()

    if not purchase:
        purchase = UserPurchase.objects.create(
            user=request.user,
            package=package,
            is_completed=False
        )

    # إنشاء عملية Telr
    transaction = TelrTransaction.objects.create(
        purchase=purchase,
        user=request.user,
        package=package,
        amount=package.effective_price,
        currency="SAR",
        status="INIT"
    )

    # تجهيز بيانات Telr
    endpoint, data = generate_telr_url(purchase, request)

    try:
        response = requests.post(endpoint, data=data, timeout=20)
        result = response.json()
    except Exception:
        transaction.status = "ERROR"
        transaction.save()
        return render(request, "payments/error.html", {
            "message": "تعذّر الاتصال ببوابة الدفع."
        })

    if "order" not in result or "url" not in result["order"]:
        transaction.status = "INVALID_RESPONSE"
        transaction.raw_response = result
        transaction.save()
        return render(request, "payments/error.html", {
            "message": "تعذّر إنشاء عملية الدفع."
        })

    # تحديث cartId الحقيقي من Telr
    transaction.order_id = result["order"]["cartid"]
    transaction.payment_url = result["order"]["url"]
    transaction.status = "PENDING"
    transaction.save()

    # تحويل المستخدم لصفحة Telr
    return redirect(result["order"]["url"])



# ============================
#   Telr Return URLs
# ============================

def telr_success(request):
    """
    صفحة النجاح بعد الدفع
    """
    purchase_id = request.GET.get("purchase")
    purchase = get_object_or_404(UserPurchase, id=purchase_id)

    purchase.expires_at = timezone.now() + timezone.timedelta(hours=72)
    purchase.is_completed = True
    purchase.save()

    # تحديد وجهة اللاعب حسب نوع اللعبة
    if purchase.package.game_type == "letters":
        next_url = f"/games/letters/create/?package_id={purchase.package.id}"
    elif purchase.package.game_type == "images":
        next_url = f"/games/images/create/?package_id={purchase.package.id}"
    else:
        next_url = "/"

    return render(request, "payments/success.html", {
        "redirect_url": next_url
    })


def telr_failed(request):
    """
    عند فشل عملية الدفع
    """
    return render(request, "payments/failed.html")


def telr_cancel(request):
    """
    عند إلغاء عملية الدفع
    """
    messages.info(request, "تم إلغاء عملية الدفع.")
    return redirect("games:home")



# ============================
#   Webhook (Callback)
# ============================

@csrf_exempt
def telr_webhook(request):
    """
    رد السيرفر من Telr — حتى لو المستخدم أغلق الصفحة
    """

    try:
        data = json.loads(request.body.decode('utf-8'))
    except:
        return HttpResponse("Invalid JSON", status=400)

    order_id = data.get("cartid")
    status = data.get("status")

    if not order_id:
        return HttpResponse("Missing cartid", status=400)

    transaction = TelrTransaction.objects.filter(order_id=order_id).first()
    if not transaction:
        return HttpResponse("Transaction not found", status=404)

    transaction.status = status
    transaction.raw_response = data
    transaction.save()

    purchase = transaction.purchase

    if status == "paid":
        purchase.is_completed = True
        purchase.expires_at = timezone.now() + timezone.timedelta(hours=72)
        purchase.save()

    return HttpResponse("OK", status=200)
