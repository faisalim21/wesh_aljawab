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

# ============================
#   إنشاء الدفع
# ============================

@login_required
def start_payment(request, package_id):
    package = get_object_or_404(GamePackage, id=package_id)

    # 1) التحقق من وجود شراء سابق غير مكتمل
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

    # 2) إنشاء order_id محلي مؤقت حتى لا يتكرر
    initial_order_id = f"local-{uuid.uuid4()}"

    # 3) إنشاء المعاملة
    transaction = TelrTransaction.objects.create(
        order_id=initial_order_id,   # هذا لن يتكرر أبداً
        purchase=purchase,
        user=request.user,
        package=package,
        amount=package.effective_price,
        currency="SAR",
        status="pending"
    )

    # 4) تجهيز بيانات Telr
    endpoint, data = generate_telr_url(purchase, request)

    try:
        response = requests.post(endpoint, data=data)
        result = response.json()
    except Exception:
        return render(request, "payments/error.html", {
            "message": "خطأ أثناء الاتصال ببوابة Telr"
        })

    # 5) التحقق من وجود رابط الدفع
    if "order" not in result or "url" not in result["order"]:
        return render(request, "payments/error.html", {
            "message": f"استجابة غير صالحة من Telr: {result}"
        })

    telr_order_id = result["order"]["cartid"]

    # 6) تحديث order_id الحقيقي من Telr
    transaction.order_id = telr_order_id
    transaction.save()

    # 7) توجيه المستخدم لصفحة الدفع
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
