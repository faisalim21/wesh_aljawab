import json
import uuid
import logging
import requests

from datetime import timedelta

from django.shortcuts import redirect, get_object_or_404, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

from games.models import GamePackage, UserPurchase, GameSession
from .models import TelrTransaction
from .telr import generate_telr_url, telr_check

logger = logging.getLogger("payments")


@login_required
def start_payment(request, package_id):
    package = get_object_or_404(GamePackage, id=package_id)

    # =========================================
    # 1️⃣ نحاول نجيب شراء غير مكتمل سابق لنفس الحزمة
    # (حتى لا نكسر unique_active_purchase_per_package)
    # =========================================
    purchase = (
        UserPurchase.objects
        .filter(
            user=request.user,
            package=package,
            is_completed=False
        )
        .order_by("-purchase_date")  # ✅ الحقل الصحيح
        .first()
    )

    if not purchase:
        purchase = UserPurchase.objects.create(
            user=request.user,
            package=package,
            is_completed=False
        )

    # =========================================
    # 2️⃣ نبحث عن TelrTransaction معلقة
    # =========================================
    tx = (
        TelrTransaction.objects
        .filter(
            purchase=purchase,
            status="pending"
        )
        .order_by("-created_at")
        .first()
    )

    if tx:
        cart_id = tx.order_id
    else:
        cart_id = f"local-{uuid.uuid4()}"
        tx = TelrTransaction.objects.create(
            order_id=cart_id,
            purchase=purchase,
            user=request.user,
            package=package,
            amount=package.effective_price,
            currency="SAR",
            status="pending",
        )

    # =========================================
    # 3️⃣ إنشاء طلب Telr
    # =========================================
    endpoint, data = generate_telr_url(purchase, request, cart_id)
    logger.info("TELR REQUEST >>> %s", json.dumps(data, ensure_ascii=False))

    try:
        response = requests.post(endpoint, data=data, timeout=20)
        result = response.json()
    except Exception as e:
        logger.exception("Telr create failed")
        messages.error(request, "فشل الاتصال ببوابة الدفع، حاول مرة أخرى.")
        return redirect(f"/games/{package.game_type}/")

    url = (result.get("order") or {}).get("url")
    if not url:
        logger.error("TELR ERROR RESPONSE: %s", result)
        messages.error(request, "حدث خطأ أثناء إنشاء عملية الدفع.")
        return redirect(f"/games/{package.game_type}/")

    # =========================================
    # 4️⃣ تحديث cartid لو Telr غيّره
    # =========================================
    telr_cartid = (result.get("order") or {}).get("cartid")
    if telr_cartid and telr_cartid != tx.order_id:
        tx.order_id = telr_cartid
        tx.save(update_fields=["order_id"])

    # =========================================
    # 5️⃣ عرض صفحة التحويل
    # =========================================
    return render(
        request,
        "payments/processing.html",
        {"payment_url": url}
    )


def _activate_purchase_and_session(purchase: UserPurchase):
    """
    تفعيل الشراء وإنشاء/جلب جلسة — idempotent وآمن ضد التكرار.
    """
    with transaction.atomic():
        purchase = (
            UserPurchase.objects.select_for_update()
            .select_related("package", "user")
            .get(id=purchase.id)
        )

        now = timezone.now()

        if not purchase.is_completed:
            purchase.is_completed = True

        if not purchase.expires_at or purchase.expires_at <= now:
            purchase.expires_at = now + timedelta(hours=72)

        purchase.save(update_fields=["is_completed", "expires_at"])

        session = GameSession.objects.select_for_update().filter(purchase=purchase).first()
        if not session:
            session = GameSession.objects.create(
                host=purchase.user,
                package=purchase.package,
                game_type=purchase.package.game_type,
                purchase=purchase,
                is_active=True
            )
        return session


from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
import time
import logging

logger = logging.getLogger("payments")


def telr_success(request):
    purchase_id = request.GET.get("purchase")
    cartid = request.GET.get("cartid")
    game_type = request.GET.get("type")

    if not purchase_id:
        messages.error(request, "بيانات الدفع ناقصة.")
        return redirect("/")

    purchase = get_object_or_404(UserPurchase, id=purchase_id)

    # لو cartid غير موجود نحاول نجيبه من آخر عملية
    if not cartid:
        tx = TelrTransaction.objects.filter(purchase=purchase).order_by("-created_at").first()
        cartid = tx.order_id if tx else None

    if not cartid:
        messages.error(request, "تعذر العثور على رقم العملية.")
        return redirect("/")

    def _is_ok(status: str) -> bool:
        status = (status or "").lower()
        return any(x in status for x in [
            "auth",
            "authorised",
            "authorized",
            "paid",
            "captured",
            "success",
            "accepted",
        ])

    # ===== 1) التحقق الأول =====
    try:
        check = telr_check(cartid)
    except Exception:
        logger.exception("Telr check failed (initial)")
        messages.error(request, "تعذر التحقق من حالة الدفع. جرّب تحديث الصفحة.")
        return redirect("/")

    order = check.get("order", {})
    status = (order.get("status") or order.get("auth") or "").lower()

    TelrTransaction.objects.filter(order_id=cartid).update(
        status=status or "unknown",
        raw_response=check
    )

    ok = _is_ok(status)

    # ===== 2) إعادة محاولة تلقائية لو الحالة متأخرة =====
    if not ok:
        time.sleep(2)

        try:
            check = telr_check(cartid)
            order = check.get("order", {})
            status = (order.get("status") or order.get("auth") or "").lower()
            ok = _is_ok(status)

            TelrTransaction.objects.filter(order_id=cartid).update(
                status=status or "unknown",
                raw_response=check
            )
        except Exception:
            logger.exception("Telr check failed (retry)")

    # ===== 3) فشل نهائي =====
    if not ok:
        messages.warning(
            request,
            "تم استلام عملية الدفع، لكن لم يتم اعتمادها بعد. "
            "إذا تم الخصم سيتم التفعيل تلقائيًا خلال دقائق."
        )
        return redirect(f"/games/{game_type}/" if game_type else "/")

    # ===== 4) نجاح مؤكد → تفعيل آمن (Idempotent) =====
    session = _activate_purchase_and_session(purchase)

    # ===== 5) إعادة التوجيه مع session =====
    return redirect(
        f"/games/{purchase.package.game_type}/?success=1&session={session.id}"
    )


def telr_failed(request):
    purchase_id = request.GET.get("purchase")
    cartid = request.GET.get("cartid")
    if cartid:
        TelrTransaction.objects.filter(order_id=cartid).update(status="failed")

    messages.error(request, "فشلت عملية الدفع، الرجاء المحاولة مرة أخرى.")
    game_type = request.GET.get("type")
    return redirect(f"/games/{game_type}/" if game_type else "/")


def telr_cancel(request):
    cartid = request.GET.get("cartid")
    if cartid:
        TelrTransaction.objects.filter(order_id=cartid).update(status="cancelled")

    messages.info(request, "تم إلغاء عملية الدفع.")
    game_type = request.GET.get("type")
    return redirect(f"/games/{game_type}/" if game_type else "/")


@csrf_exempt
def telr_webhook(request):
    """
    Telr webhook: قد يجي JSON أو POST form.
    نقرأ الاثنين + نعمل check(cartid) للتأكيد قبل التفعيل.
    """
    cartid = None

    # 1) جرّب JSON
    if request.body:
        try:
            data = json.loads(request.body.decode("utf-8"))
            cartid = data.get("cartid") or data.get("ivp_cart") or data.get("order_id")
        except Exception:
            data = {}

    # 2) جرّب form POST
    if not cartid:
        cartid = request.POST.get("cartid") or request.POST.get("ivp_cart") or request.POST.get("order_id")

    if not cartid:
        return HttpResponse("Missing cartid", status=400)

    tx = TelrTransaction.objects.filter(order_id=cartid).select_related("purchase").first()
    if not tx:
        return HttpResponse("Transaction not found", status=404)

    # تأكيد من Telr
    try:
        check = telr_check(cartid)
        order = (check.get("order") or {})
        status = (order.get("status") or order.get("auth") or "").strip().lower()
    except Exception as e:
        logger.exception("Telr check failed (webhook)")
        return HttpResponse("Check failed", status=500)

    tx.status = status or "unknown"
    tx.raw_response = check
    tx.save(update_fields=["status", "raw_response"])

    ok = any(x in (status or "") for x in ["paid", "authorised", "authorized", "auth", "captured", "success", "accepted"])
    if ok:
        _activate_purchase_and_session(tx.purchase)

    return HttpResponse("OK", status=200)
