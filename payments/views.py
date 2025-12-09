import requests
import json
import uuid
from django.shortcuts import redirect, get_object_or_404, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
import logging
logger = logging.getLogger("payments")
from games.models import GameSession
from games.models import GamePackage, UserPurchase
from .telr import generate_telr_url
from .models import TelrTransaction


# ============================
#   إنشاء الدفع
# ============================

@login_required
def start_payment(request, package_id):
    package = get_object_or_404(GamePackage, id=package_id)

    # STEP 1 — تحديث المشتريات المنتهية
    old_purchases = UserPurchase.objects.filter(
        user=request.user, package=package, is_completed=False
    )
    for p in old_purchases:
        if p.is_expired:
            p.is_completed = True
            p.save(update_fields=['is_completed'])

    # STEP 2 — التحقق من وجود عملية دفع معلقة
    from .models import TelrTransaction
    pending_tx = TelrTransaction.objects.filter(
        user=request.user,
        package=package,
        status="pending",
        purchase__is_completed=False
    ).first()

    if pending_tx:
        purchase = pending_tx.purchase
    else:
        # إنشاء Purchase جديدة (لا تعتبر شراء حقيقي إلا بعد success)
        purchase = UserPurchase.objects.create(
            user=request.user,
            package=package,
            is_completed=False
        )

        # إنشاء Transaction جديدة
        initial_order_id = f"local-{uuid.uuid4()}"
        pending_tx = TelrTransaction.objects.create(
            order_id=initial_order_id,
            purchase=purchase,
            user=request.user,
            package=package,
            amount=package.effective_price,
            currency="SAR",
            status="pending"
        )

    # STEP 3 — تجهيز وإرسال طلب Telr
    endpoint, data = generate_telr_url(purchase, request, pending_tx.order_id)
    logger.info("TELR REQUEST PAYLOAD >>> " + json.dumps(data, ensure_ascii=False))

    try:
        response = requests.post(endpoint, data=data, timeout=15)
        result = response.json()
    except Exception as e:
        return render(request, "payments/error.html", {
            "message": f"فشل الاتصال بـ Telr: {str(e)}"
        })

    if "order" not in result or "url" not in result["order"]:
        return render(request, "payments/error.html", {
            "message": json.dumps(result, ensure_ascii=False, indent=2)
        })

    # تحديث order_id من Telr
    pending_tx.order_id = result["order"].get("cartid", pending_tx.order_id)
    pending_tx.save()

    return render(request, "payments/processing.html", {
        "payment_url": result["order"]["url"]
    })



# ============================
#   Telr Return URLs
# ============================

def telr_success(request):
    purchase_id = request.GET.get("purchase")
    purchase = get_object_or_404(UserPurchase, id=purchase_id)

    purchase.is_completed = True
    purchase.expires_at = timezone.now() + timezone.timedelta(hours=72)
    purchase.save()

    # إنشاء أو استرجاع الجلسة
    session = GameSession.objects.filter(purchase=purchase).first()
    if not session:
        session = GameSession.objects.create(
            host=purchase.user,
            package=purchase.package,
            game_type=purchase.package.game_type,
            purchase=purchase
        )

    # بدل redirect نمرر الرابط للتمبلت
    game_url = f"/games/{purchase.package.game_type}/session/{session.id}/"

    return render(request, "payments/success.html", {
        "game_url": game_url
    })




def telr_failed(request):
    messages.error(request, "فشلت عملية الدفع، الرجاء المحاولة مرة أخرى.")

    game_type = request.GET.get("type")

    if game_type == "letters":
        return redirect("/games/letters/")
    if game_type == "images":
        return redirect("/games/images/")

    return redirect("/")



def telr_cancel(request):
    messages.info(request, "تم إلغاء عملية الدفع.")

    game_type = request.GET.get("type")

    if game_type == "letters":
        return redirect("/games/letters/")
    if game_type == "images":
        return redirect("/games/images/")

    return redirect("/")


# ============================
#   Webhook
# ============================

@csrf_exempt
def telr_webhook(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
    except:
        return HttpResponse("Invalid JSON", status=400)

    order_id = data.get("cartid")
    status = data.get("status")

    if not order_id:
        return HttpResponse("Missing order id", status=400)

    transaction = TelrTransaction.objects.filter(order_id=order_id).first()
    if not transaction:
        return HttpResponse("Transaction not found", status=404)

    # حفظ الرد
    transaction.status = status
    transaction.raw_response = data
    transaction.save()

    purchase = transaction.purchase
    if status == "paid":
        purchase.is_completed = True
        purchase.expires_at = timezone.now() + timezone.timedelta(hours=72)
        purchase.save()

    return HttpResponse("OK", status=200)
