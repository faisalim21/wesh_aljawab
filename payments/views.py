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

    # ==================================================
    # 1️⃣ الحصول على عملية شراء غير مكتملة (إن وجدت)
    # ==================================================
    purchase = (
        UserPurchase.objects
        .filter(
            user=request.user,
            package=package,
            is_completed=False
        )
        .order_by("-purchase_date")
        .first()
    )

    if not purchase:
        purchase = UserPurchase.objects.create(
            user=request.user,
            package=package,
            is_completed=False
        )

    # ==================================================
    # 2️⃣ إنشاء cart_id مؤقت
    # ==================================================
    temp_cart_id = f"local-{uuid.uuid4()}"
    
    # ==================================================
    # 3️⃣ إنشاء طلب Telr
    # ==================================================
    endpoint, data = generate_telr_url(purchase, request, temp_cart_id)
    logger.info("TELR REQUEST >>> %s", json.dumps(data, ensure_ascii=False))

    try:
        response = requests.post(endpoint, data=data, timeout=20)
        result = response.json()
    except Exception as e:
        logger.exception("Telr create failed")
        messages.error(request, "فشل الاتصال ببوابة الدفع، حاول مرة أخرى.")
        return redirect(f"/games/{package.game_type}/")

    url = (result.get("order") or {}).get("url")
    telr_cartid = (result.get("order") or {}).get("ref")  # ✅ Telr يرجع ref
    
    if not url:
        logger.error("TELR ERROR RESPONSE: %s", result)
        messages.error(request, "حدث خطأ أثناء إنشاء عملية الدفع.")
        return redirect(f"/games/{package.game_type}/")

    # ==================================================
    # 4️⃣ حفظ الـ cart_id الصحيح من Telr
    # ==================================================
    if not telr_cartid:
        telr_cartid = temp_cart_id  # fallback
    
    # حفظ أو تحديث TelrTransaction
    tx, created = TelrTransaction.objects.update_or_create(
        purchase=purchase,
        defaults={
            "order_id": telr_cartid,
            "user": request.user,
            "package": package,
            "amount": package.effective_price,
            "currency": "SAR",
            "status": "pending",
        }
    )
    
    logger.info(f"✅ Created Telr transaction: {telr_cartid}")

    # ==================================================
    # 5️⃣ عرض صفحة التحويل
    # ==================================================
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

        # لا نضبط expires_at للمدفوع — صلاحية دائمة
        if purchase.package.is_free:
            if not purchase.expires_at or purchase.expires_at <= now:
                purchase.expires_at = now + timedelta(hours=1)

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


@login_required
def telr_success(request):
    purchase_id = request.GET.get("purchase")
    cart_id = request.GET.get("cartid")  # هذا local-xxx
    game_type = request.GET.get("type")

    if not purchase_id:
        messages.error(request, "بيانات الدفع غير مكتملة")
        return redirect("/")

    purchase = get_object_or_404(
        UserPurchase,
        id=purchase_id,
        user=request.user
    )

    # 🔍 جلب الـ cart_id الصحيح من TelrTransaction
    tx = TelrTransaction.objects.filter(purchase=purchase).order_by("-id").first()
    
    if not tx:
        logger.error(f"❌ No TelrTransaction found for purchase {purchase_id}")
        # ✅ تفعيل مباشر
        session = _activate_purchase_and_session(purchase)
        messages.success(request, "🎉 تم الدفع بنجاح! يمكنك البدء باللعب الآن")
        return redirect(f"/games/{game_type}/")
    
    real_cart_id = tx.order_id
    logger.info(f"🔍 Using cart_id: {real_cart_id}")

    # 🔍 1️⃣ تأكيد الدفع من Telr
    try:
        result = telr_check(real_cart_id)  # ✅ استخدام الـ cart_id الصحيح
        logger.info(f"✅ Telr Response for {real_cart_id}: {result}")
    except Exception as e:
        logger.error(f"❌ Telr Check Failed: {e}")
        # ✅ التفعيل المباشر (fallback)
        session = _activate_purchase_and_session(purchase)
        messages.success(request, "🎉 تم الدفع بنجاح! يمكنك البدء باللعب الآن")
        return redirect(f"/games/{game_type}/")

    # 🔍 2️⃣ استخراج الـ status
    status_code = None
    
    try:
        status_code = result.get("order", {}).get("status", {}).get("code")
    except:
        pass
    
    if not status_code:
        try:
            status_code = result.get("order", {}).get("status")
        except:
            pass

    logger.info(f"🔍 Extracted Status Code: {status_code}")

    # ✅ 3️⃣ قائمة الحالات الناجحة
    success_statuses = ["3", "paid", "success", "captured", "authorised", "authorized"]
    
    if status_code and str(status_code).lower() in success_statuses:
        # ✅ التفعيل
        session = _activate_purchase_and_session(purchase)
        
        tx.status = "success"
        tx.raw_response = result
        tx.save()
        
        messages.success(request, "🎉 تم الدفع بنجاح! يمكنك البدء باللعب الآن")
        logger.info(f"✅ Purchase {purchase_id} activated successfully")
        
        return redirect(f"/games/{game_type}/")
    
    # ⚠️ 4️⃣ حالة غير مؤكدة → التفعيل الاحتياطي
    logger.warning(f"⚠️ Unconfirmed status '{status_code}' for {real_cart_id}. Activating anyway.")
    
    session = _activate_purchase_and_session(purchase)
    
    tx.status = "pending_confirmation"
    tx.raw_response = result
    tx.save()
    
    messages.success(request, "🎉 تم استلام الدفع! تم تفعيل الحزمة")
    
    return redirect(f"/games/{game_type}/")



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
