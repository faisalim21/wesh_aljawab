import requests
import json
import uuid
import logging
from datetime import timedelta

from django.shortcuts import redirect, get_object_or_404, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

from games.models import GameSession, GamePackage, UserPurchase
from .models import TelrTransaction
from .telr import generate_telr_url

logger = logging.getLogger("payments")

# =========================================================
# إنشاء الدفع
# =========================================================

@login_required
def start_payment(request, package_id):
    package = get_object_or_404(GamePackage, id=package_id)

    # تنظيف أي عمليات قديمة غير مكتملة
    UserPurchase.objects.filter(
        user=request.user,
        package=package,
        is_completed=False,
        expires_at__lt=timezone.now()
    ).update(is_completed=True)

    # البحث عن عملية معلّقة
    pending_tx = TelrTransaction.objects.filter(
        user=request.user,
        package=package,
        status="pending",
        purchase__is_completed=False
    ).select_related("purchase").first()

    if pending_tx:
        purchase = pending_tx.purchase
    else:
        purchase = UserPurchase.objects.create(
            user=request.user,
            package=package,
            is_completed=False
        )

        pending_tx = TelrTransaction.objects.create(
            order_id=f"local-{uuid.uuid4()}",
            purchase=purchase,
            user=request.user,
            package=package,
            amount=package.effective_price,
            currency="SAR",
            status="pending"
        )

    endpoint, payload = generate_telr_url(purchase, request, pending_tx.order_id)
    logger.info("TELR REQUEST >>> %s", json.dumps(payload, ensure_ascii=False))

    try:
        response = requests.post(endpoint, data=payload, timeout=15)
        result = response.json()
    except Exception as e:
        logger.error("TELR CONNECTION ERROR: %s", e)
        return render(request, "payments/error.html", {
            "message": "فشل الاتصال ببوابة الدفع"
        })

    if "order" not in result or "url" not in result["order"]:
        logger.error("TELR INVALID RESPONSE: %s", result)
        return render(request, "payments/error.html", {
            "message": "رد غير صالح من بوابة الدفع"
        })

    pending_tx.order_id = result["order"].get("cartid", pending_tx.order_id)
    pending_tx.save(update_fields=["order_id"])

    return render(request, "payments/processing.html", {
        "payment_url": result["order"]["url"]
    })


# =========================================================
# Return URLs (عرض فقط – بدون اعتماد)
# =========================================================

def telr_success(request):
    purchase_id = request.GET.get("purchase")
    game_type = request.GET.get("type")

    if not purchase_id:
        return redirect("/")

    purchase = get_object_or_404(UserPurchase, id=purchase_id)

    # ❗ لا نكمل الشراء هنا
    if not purchase.is_completed:
        messages.info(request, "تم استلام الدفع، بانتظار التأكيد النهائي...")
        return redirect(_redirect_by_game(game_type))

    session = GameSession.objects.filter(purchase=purchase).first()
    if session:
        return redirect(f"/games/{game_type}/?success=1&session={session.id}")

    return redirect(_redirect_by_game(game_type))


def telr_failed(request):
    messages.error(request, "فشلت عملية الدفع.")
    return redirect(_redirect_by_game(request.GET.get("type")))


def telr_cancel(request):
    messages.info(request, "تم إلغاء عملية الدفع.")
    return redirect(_redirect_by_game(request.GET.get("type")))


def _redirect_by_game(game_type):
    if game_type == "letters":
        return "/games/letters/"
    if game_type == "images":
        return "/games/images/"
    if game_type == "imposter":
        return "/games/imposter/"
    return "/"


# =========================================================
# Webhook (المصدر الحقيقي للحقيقة)
# =========================================================

@csrf_exempt
def telr_webhook(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponse("Invalid JSON", status=400)

    order_id = data.get("cartid")
    status = (data.get("status") or "").lower()

    if not order_id:
        return HttpResponse("Missing order id", status=400)

    tx = TelrTransaction.objects.select_related(
        "purchase", "purchase__package", "purchase__user"
    ).filter(order_id=order_id).first()

    if not tx:
        return HttpResponse("Transaction not found", status=404)

    tx.raw_response = data
    tx.status = status
    tx.save(update_fields=["status", "raw_response"])

    if status in ("paid", "captured"):
        with transaction.atomic():
            purchase = UserPurchase.objects.select_for_update().get(id=tx.purchase_id)

            if not purchase.is_completed:
                purchase.is_completed = True
                purchase.expires_at = timezone.now() + timedelta(hours=72)
                purchase.save(update_fields=["is_completed", "expires_at"])

                GameSession.objects.get_or_create(
                    purchase=purchase,
                    defaults={
                        "host": purchase.user,
                        "package": purchase.package,
                        "game_type": purchase.package.game_type,
                        "is_active": True,
                    }
                )

    return HttpResponse("OK", status=200)
