import requests
import json
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render

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
    package = get_object_or_404(GamePackage, id=package_id)

    # التأكد من عدم وجود عملية شراء مفتوحة مسبقًا
    existing = UserPurchase.objects.filter(
        user=request.user,
        package=package,
        is_completed=False
    ).first()

    if existing:
        purchase = existing
    else:
        purchase = UserPurchase.objects.create(
            user=request.user,
            package=package,
            is_completed=False
        )

    # تسجيل المعاملة
    trans = TelrTransaction.objects.create(
        order_id=str(purchase.id),
        purchase=purchase,
        user=request.user,
        package=package,
        amount=package.effective_price,
        currency="SAR",
        status="pending"
    )

    # تجهيز بيانات Telr
    endpoint, data = generate_telr_url(purchase, request)

    try:
        response = requests.post(endpoint, data=data)
        result = response.json()
    except Exception:
        messages.error(request, "فشل الاتصال ببوابة Telr.")
        return redirect("games:home")

    if "order" not in result or "url" not in result["order"]:
        messages.error(request, "فشل في إنشاء عملية الدفع.")
        return redirect("games:home")

    # تحديث order_id الحقيقي
    trans.order_id = result["order"]["cartid"]
    trans.save()

    return redirect(result["order"]["url"])

# ============================
#   Telr Return URLs
# ============================

def telr_success(request):
    purchase_id = request.GET.get("purchase")
    purchase = get_object_or_404(UserPurchase, id=purchase_id)

    purchase.expires_at = timezone.now() + timezone.timedelta(hours=72)
    purchase.is_completed = True
    purchase.save()

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
    messages.error(request, "فشلت عملية الدفع.")
    return render(request, "payments/failed.html")


def telr_cancel(request):
    messages.info(request, "تم إلغاء عملية الدفع.")
    return redirect("games:home")


# ============================
#   Webhook (Callback)
# ============================

@csrf_exempt
def telr_webhook(request):
    """
    يستقبل رد Telr النهائي (Server to Server)
    حتى لو المستخدم أغلق المتصفح.
    """

    try:
        data = json.loads(request.body.decode('utf-8'))
    except:
        return HttpResponse("Invalid JSON", status=400)

    order_id = data.get("cartid")
    status = data.get("status")

    if not order_id:
        return HttpResponse("Missing order id", status=400)

    # جلب المعاملة
    trans = TelrTransaction.objects.filter(order_id=order_id).first()
    if not trans:
        return HttpResponse("Transaction not found", status=404)

    # تحديث بيانات العملية
    trans.status = status
    trans.raw_response = data
    trans.save()

    # تحديث UserPurchase
    purchase = trans.purchase
    if status == "paid":
        purchase.is_completed = True
        purchase.expires_at = timezone.now() + timezone.timedelta(hours=72)
        purchase.save()

    return HttpResponse("OK", status=200)
