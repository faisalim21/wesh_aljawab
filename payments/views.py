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

    # 1) لو عنده عملية pending قديمة لنفس الحزمة، نعيد استخدامها (نفس cartid)
    pending_tx = (
        TelrTransaction.objects
        .select_related("purchase")
        .filter(user=request.user, package=package, status="pending")
        .order_by("-created_at")
        .first()
    )

    if pending_tx:
        purchase = pending_tx.purchase
        cart_id = pending_tx.order_id  # نخلي order_id عندنا = cartid
    else:
        purchase = UserPurchase.objects.create(
            user=request.user,
            package=package,
            is_completed=False
        )
        cart_id = f"local-{uuid.uuid4()}"
        pending_tx = TelrTransaction.objects.create(
            order_id=cart_id,
            purchase=purchase,
            user=request.user,
            package=package,
            amount=package.effective_price,
            currency="SAR",
            status="pending",
        )

    endpoint, data = generate_telr_url(purchase, request, cart_id)
    logger.info("TELR REQUEST >>> " + json.dumps(data, ensure_ascii=False))

    try:
        response = requests.post(endpoint, data=data, timeout=20)
        result = response.json()
    except Exception as e:
        logger.exception("Telr create failed")
        return render(request, "payments/error.html", {"message": f"فشل الاتصال بـ Telr: {str(e)}"})

    url = (result.get("order") or {}).get("url")
    if not url:
        return render(request, "payments/error.html", {
            "message": json.dumps(result, ensure_ascii=False, indent=2)
        })

    # (اختياري) Telr أحياناً يرجع cartid جديد، لكن إحنا ثابتين على cart_id
    telr_cartid = (result.get("order") or {}).get("cartid")
    if telr_cartid and telr_cartid != pending_tx.order_id:
        pending_tx.order_id = telr_cartid
        pending_tx.save(update_fields=["order_id"])

    return render(request, "payments/processing.html", {"payment_url": url})


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


def telr_success(request):
    purchase_id = request.GET.get("purchase")
    cartid = request.GET.get("cartid")  # مهم
    game_type = request.GET.get("type")

    if not purchase_id:
        messages.error(request, "بيانات الدفع ناقصة (purchase).")
        return redirect("/")

    purchase = get_object_or_404(UserPurchase, id=purchase_id)

    # إذا ما جاني cartid من return، أحاول أجيبه من TelrTransaction
    if not cartid:
        tx = TelrTransaction.objects.filter(purchase=purchase).order_by("-created_at").first()
        cartid = tx.order_id if tx else None

    if not cartid:
        messages.error(request, "لم يتم العثور على رقم العملية (cartid).")
        return redirect("/")

    # ✅ تحقق من Telr فعلياً
    try:
        check = telr_check(cartid)
    except Exception as e:
        logger.exception("Telr check failed")
        messages.error(request, "تعذر التحقق من حالة الدفع من Telr. جرّب تحديث الصفحة.")
        return redirect("/")

    order = (check.get("order") or {})
    # بعض حسابات Telr ترجع status كنص/كود — نعامل أي نجاح بشكل مرن
    status = (order.get("status") or order.get("auth") or "").strip().lower()

    # سجّل الحالة في جدول المعاملات
    TelrTransaction.objects.filter(order_id=cartid).update(
        status=status or "unknown",
        raw_response=check
    )

    # اعتبرها ناجحة إذا ظهر auth/pass أو status فيه paid/authorised
    ok = any(x in status for x in ["paid", "authorised", "authorized", "auth", "captured", "success", "accepted"])
    if not ok:
        messages.error(request, f"الدفع لم يُعتمد بعد (status={status}).")
        # رجّعه لنفس صفحة الحزم
        return redirect(f"/games/{game_type}/" if game_type else "/")

    session = _activate_purchase_and_session(purchase)

    # توجه لصفحة الحزم عشان يطلع زر ابدأ اللعب
    if purchase.package.game_type == "letters":
        return redirect(f"/games/letters/?success=1&session={session.id}")
    if purchase.package.game_type == "images":
        return redirect(f"/games/images/?success=1&session={session.id}")
    if purchase.package.game_type == "imposter":
        return redirect(f"/games/imposter/?success=1&session={session.id}")

    return redirect("/")


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
