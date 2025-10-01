# payments/views.py
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.db import IntegrityError, transaction as db_txn
from django.views.decorators.csrf import csrf_exempt
from django.utils.crypto import get_random_string
# payments/views.py  (أضِف الاستيرادين دول أعلى الملف)
import json
import requests
from urllib.parse import urljoin

from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from uuid import UUID
import logging
import os

from games.models import GamePackage, UserPurchase
from .models import Transaction, PaymentMethod, FakePaymentGateway, Invoice
from accounts.models import UserActivity

# تشفير trandata عبر rajhi_crypto (يُحدد الخوارزمية من المتغيرات: RAJHI_TRANDATA_ALGO = 3DES/AES)
from .rajhi_crypto import encrypt_trandata, decrypt_trandata

logger = logging.getLogger("payments.views")

# بوابات الراجحي
GATEWAY_URL_PROD = "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"
GATEWAY_URL_UAT  = "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"


def payments_home(request):
    """الصفحة الرئيسية لقسم المدفوعات."""
    return render(request, "payments/home.html")


@login_required
def purchase_package(request, package_id):
    """
    شراء حزمة (بوابة وهمية للتطوير). لاحقًا يمكن تحويله لتدفق الراجحي.
    """
    package = get_object_or_404(GamePackage, id=package_id, is_active=True)

    if package.is_free:
        messages.info(request, 'هذه حزمة مجانية — لا حاجة للشراء.')
        return redirect('games:letters_home')

    existing_purchase = UserPurchase.objects.filter(
        user=request.user, package=package, is_completed=False
    ).first()
    if existing_purchase:
        messages.warning(request, 'لديك هذه الحزمة بالفعل.')
        return redirect('games:letters_home')

    payment_methods = PaymentMethod.objects.filter(is_active=True)

    if request.method == 'POST':
        payment_method_id = request.POST.get('payment_method')
        payment_method = get_object_or_404(PaymentMethod, id=payment_method_id, is_active=True)

        txn = Transaction.objects.create(
            user=request.user,
            package=package,
            amount=package.price,
            payment_method=payment_method,
            status='pending'
        )

        gateway = FakePaymentGateway.objects.filter(is_active=True).first()
        if not gateway:
            messages.error(request, 'بوابة الدفع غير متوفرة حالياً.')
            return redirect('payments:cancel')

        success = gateway.process_payment(txn)

        if success:
            try:
                with db_txn.atomic():
                    UserPurchase.objects.get_or_create(user=request.user, package=package)
            except IntegrityError:
                pass

            try:
                UserActivity.objects.create(
                    user=request.user,
                    activity_type='package_purchased',
                    description=f'شراء حزمة {package.get_game_type_display()} - حزمة {package.package_number}'
                )
            except Exception as e:
                logger.warning("Failed to log UserActivity after fake gateway success: %s", e)

            messages.success(request, 'تم الشراء بنجاح! يمكنك الآن اللعب.')
            return redirect('games:letters_home')
        else:
            messages.error(request, 'فشل في عملية الدفع، يرجى المحاولة مرة أخرى.')
            return redirect('payments:cancel')

    return render(request, 'payments/purchase.html', {
        'package': package,
        'payment_methods': payment_methods,
    })


@login_required
def payment_success(request):
    last_transaction = Transaction.objects.filter(
        user=request.user, status='completed'
    ).order_by('-completed_at').first()
    return render(request, 'payments/success.html', {'transaction': last_transaction})


def payment_cancel(request):
    return render(request, 'payments/cancel.html')


@login_required
def transaction_history(request):
    txns = Transaction.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'payments/history.html', {'transactions': txns})


@login_required
def invoice_view(request, transaction_id):
    txn = get_object_or_404(Transaction, id=transaction_id, user=request.user)
    try:
        invoice = txn.invoice
    except Invoice.DoesNotExist:
        messages.error(request, 'لم يتم العثور على الفاتورة')
        return redirect('payments:history')
    return render(request, 'payments/invoice.html', {'invoice': invoice, 'transaction': txn})


# =========================
# اختبارات تكوين بوابة الراجحي
# =========================
def rajhi_test(request):
    """
    صفحة اختبار سريعة تعرض قيم الإعدادات المطلوبة ووجود ملف resource.
    """
    cfg = getattr(settings, "RAJHI_CONFIG", {})
    required = ("MERCHANT_ID", "TERMINAL_ID", "TRANSPORTAL_ID", "TRANSPORTAL_PASSWORD")
    missing = [k for k in required if not (cfg.get(k) or "").strip()]

    # resource: ملف أو متغير بيئة
    resource_exists = False
    resource_path = cfg.get("RESOURCE_FILE") or ""
    if resource_path:
        try:
            resource_exists = os.path.isfile(resource_path)
        except Exception:
            resource_exists = False

    safe_cfg = {
        "MERCHANT_ID": cfg.get("MERCHANT_ID"),
        "TERMINAL_ID": cfg.get("TERMINAL_ID"),
        "TRANSPORTAL_ID": cfg.get("TRANSPORTAL_ID"),
        "TRANSPORTAL_PASSWORD_SET": bool((cfg.get("TRANSPORTAL_PASSWORD") or "").strip()),
        "RESOURCE_FILE": resource_path,
        "RESOURCE_FILE_EXISTS": resource_exists,
        "RESOURCE_KEY_SET": bool((cfg.get("RESOURCE_KEY") or "").strip()),
    }
    return render(request, "payments/test.html", {
        "ok": len(missing) == 0 and (resource_exists or bool(cfg.get("RESOURCE_KEY"))),
        "missing_keys": missing,
        "config_preview": safe_cfg,
    })



# =========================
#  تدفّق التهيئة المباشر (المعتمد)
# =========================
# payments/views.py
from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.utils.crypto import get_random_string
from payments.rajhi_crypto import encrypt_trandata

def rajhi_direct_init(request):
    """
    يبني trandata بالقيم الصحيحة (ResponseURL / ErrorURL)
    ويرسل فورم مخفي لبوابة الراجحي (UAT أو PROD حسب ?env=uat).
    """
    # اختر بيئة البوابة
    env = (request.GET.get("env") or "").lower()
    is_uat = env == "uat"
    gateway_url = (
        "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"
        if not is_uat else
        "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"
        # ملاحظة: غالبًا نفس الدومين، لكن الـ Profile بالحساب يحدد UAT/PROD.
        # إن كان عندك URL مختلف للـ UAT من الراجحي، ضعّه هنا.
    )

    cfg = getattr(settings, "RAJHI_CONFIG", {})
    tranportal_id = cfg.get("TRANSPORTAL_ID", "").strip()
    tranportal_password = cfg.get("TRANSPORTAL_PASSWORD", "").strip()

    # نبني روابط مطلقة https للـ callbacks
    success_url = request.build_absolute_uri(reverse("payments:rajhi_callback_success"))
    fail_url    = request.build_absolute_uri(reverse("payments:rajhi_callback_fail"))

    # trackid عشوائي قصير
    trackid = get_random_string(12, allowed_chars="0123456789")

    # IMPORTANT: المفاتيح بحالة الأحرف كما تتوقع البوابة
    trandata_pairs = {
        "action": "1",                 # 1 = Purchase
        "amt": request.GET.get("amt", "5.00"),
        "currencycode": "682",         # SAR
        "langid": "AR",
        "trackid": trackid,
        "ResponseURL": success_url,    # ✅ حالة أحرف صحيحة
        "ErrorURL": fail_url,          # ✅ حالة أحرف صحيحة
        "udf1": "",
        "udf2": "",
        "udf3": "",
        "udf4": "",
        "udf5": "",
    }

    trandata_hex = encrypt_trandata(trandata_pairs)

    # صفحة auto-post
    context = {
        "gateway_url": gateway_url,
        "id": tranportal_id,
        "password": tranportal_password,
        "trandata": trandata_hex,

        # للتشخيص عند الحاجة: ?debug=1
        "debug": (request.GET.get("debug") == "1"),
        "debug_plain": "&".join(f"{k}={v}" for k, v in trandata_pairs.items()),
    }
    return render(request, "payments/rajhi_direct_init.html", context)


# =========================
#  نقاط الرجوع (Callback) — مع فك تشفير trandata
# =========================
def _extract_trandata(request):
    """
    يحاول أخذ trandata من POST أو GET ثم يفك تشفيره إن وُجد.
    يُرجع tuple: (plain_str, params_dict) أو (None, {})
    """
    enc = request.POST.get("trandata") or request.GET.get("trandata")
    if not enc:
        return None, {}
    try:
        plain, params = decrypt_trandata(enc)
        return plain, params
    except Exception as e:
        # لا نُظهر السرّيات للمستخدم
        logger.error("RAJHI_CALLBACK_DECRYPT_ERR: %s", repr(e))
        return None, {}


def _parse_decimal_safe(v: str) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


@csrf_exempt
def rajhi_callback_success(request):
    """
    مسار نجاح من الراجحي. يطبّق:
      - فك trandata والتحقق منها
      - idempotency عبر قفل صف المعاملة select_for_update
      - التحقق من المبلغ amt يطابق قيمة المعاملة
      - إتمام المعاملة وإنشاء UserPurchase وتسجيل UserActivity داخل atomic()
    """
    plain, params = _extract_trandata(request)
    if not params:
        return HttpResponseBadRequest("Missing/invalid trandata")

    result    = (params.get("result") or "").upper()
    paymentid = params.get("paymentid") or params.get("paymentId") or ""
    trackid   = params.get("trackid") or ""
    udf1_user = params.get("udf1") or ""
    udf2_txn  = params.get("udf2") or ""  # transaction.id لو أرسلناه
    amt_str   = params.get("amt") or params.get("amount") or ""
    amt_dec   = _parse_decimal_safe(amt_str)

    # نحاول الوصول للمعاملة بدقة أعلى
    if udf2_txn:
        txn_qs = Transaction.objects.filter(id=udf2_txn)
    elif udf1_user.isdigit():
        txn_qs = Transaction.objects.filter(user_id=int(udf1_user)).order_by('-created_at')
    else:
        txn_qs = Transaction.objects.none()

    if not txn_qs.exists():
        logger.error("Callback success: transaction not found. udf2=%s udf1=%s trackid=%s", udf2_txn, udf1_user, trackid)
        return HttpResponse("Transaction not found", status=200)

    with db_txn.atomic():
        # قفل السجل لتجنّب السباق
        txn = txn_qs.select_for_update().first()

        # idempotency: إن كانت مكتملة مسبقًا لا نعيد المعالجة
        if txn.status == 'completed':
            logger.info("Callback success: idempotent hit (already completed). txn=%s", txn.id)
            return HttpResponse("Already completed", status=200)

        # تحقّق من النتيجة
        if result not in ("CAPTURED", "APPROVED", "SUCCESS", "SUCCESSFUL"):
            txn.status = 'failed'
            txn.failure_reason = params.get("errorText") or params.get("error") or "Transaction not approved"
            txn.gateway_response = params
            txn.save(update_fields=['status', 'failure_reason', 'gateway_response', 'updated_at'])
            logger.warning("Callback success endpoint with non-success result=%s txn=%s", result, txn.id)
            return HttpResponse("Not approved", status=200)

        # تحقّق من المبلغ (إن توفر)
        if amt_dec is not None and txn.amount is not None and amt_dec != txn.amount:
            txn.status = 'failed'
            txn.failure_reason = f"Amount mismatch: got {amt_dec} expected {txn.amount}"
            txn.gateway_response = params
            txn.save(update_fields=['status', 'failure_reason', 'gateway_response', 'updated_at'])
            logger.error("Amount mismatch: txn=%s amt_cb=%s amt_txn=%s", txn.id, amt_dec, txn.amount)
            return HttpResponse("Amount mismatch", status=200)

        # تحديث حالة المعاملة
        txn.status = 'completed'
        txn.completed_at = timezone.now()
        if paymentid:
            txn.gateway_transaction_id = paymentid
        txn.gateway_response = params
        txn.save(update_fields=['status', 'completed_at', 'gateway_transaction_id', 'gateway_response', 'updated_at'])

        # إنشاء UserPurchase بأمان (idempotent)
        try:
            UserPurchase.objects.get_or_create(user=txn.user, package=txn.package)
        except IntegrityError:
            pass

        # تسجيل نشاط المستخدم
        try:
            UserActivity.objects.create(
                user=txn.user,
                activity_type='package_purchased',
                description=f'شراء حزمة {txn.package.get_game_type_display()} - حزمة {txn.package.package_number}'
            )
        except Exception as e:
            logger.warning("Failed to log UserActivity on success callback: %s", e)

    return HttpResponse("Rajhi callback (success).", status=200)


@csrf_exempt
def rajhi_callback_fail(request):
    """
    مسار الفشل/الإلغاء من الراجحي.
    يحدّث حالة المعاملة إلى failed (idempotent) ويخزّن سبب الفشل.
    """
    plain, params = _extract_trandata(request)
    udf1_user = (params.get("udf1") or "")
    udf2_txn  = (params.get("udf2") or "")

    try:
        if udf2_txn:
            txn_qs = Transaction.objects.filter(id=udf2_txn)
        elif udf1_user.isdigit():
            txn_qs = Transaction.objects.filter(user_id=int(udf1_user)).order_by('-created_at')
        else:
            txn_qs = Transaction.objects.none()

        if not txn_qs.exists():
            logger.error("Callback fail: transaction not found. udf2=%s udf1=%s", udf2_txn, udf1_user)
            return HttpResponse("Transaction not found", status=200)

        with db_txn.atomic():
            txn = txn_qs.select_for_update().first()
            if txn.status == 'completed':
                logger.info("Callback fail received but txn already completed. txn=%s", txn.id)
                return HttpResponse("Already completed", status=200)

            txn.status = 'failed'
            txn.failure_reason = (params.get("errorText") or params.get("error") or "User canceled/failed")
            txn.gateway_response = params
            txn.save(update_fields=['status', 'failure_reason', 'gateway_response', 'updated_at'])
    except Exception as e:
        logger.error("Callback fail processing error: %s", e)

    return HttpResponse("Rajhi callback (fail).", status=200)


# =========================
#  صفحة تجربة قديمة (ارسال trandata)
# =========================
def rajhi_checkout(request):
    """
    صفحة اختبار: إرسال trandata مع Tranportal ID/Password للبوابة.
    """
    cfg = settings.RAJHI_CONFIG
    tranportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
    tranportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

    # اختيار البيئة: UAT عند ?env=uat أو إذا كان DEBUG
    use_uat = (request.GET.get("env", "").lower() == "uat") or settings.DEBUG
    action_url = (
        "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"
        if not use_uat else
        "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"
    )

    amount  = (request.GET.get("amt") or "3.00").strip()
    trackid = (request.GET.get("t") or get_random_string(12, allowed_chars="0123456789")).strip()

    # روابط الرجوع
    base_cb = (os.environ.get("PUBLIC_BASE_URL") or request.build_absolute_uri('/').rstrip('/')).rstrip('/')
    if base_cb.startswith("http://"):
        base_cb = "https://" + base_cb[len("http://"):]
    success_url = f"{base_cb}/payments/rajhi/callback/success/"
    fail_url    = f"{base_cb}/payments/rajhi/callback/fail/"

    # مهم: إدخال Tranportal ID + Password داخل trandata
    trandata_pairs = {
        "id":          tranportal_id,
        "password":    tranportal_password,
        "action":      "1",
        "amt":         amount,
        "currencyCode": "682",   # ← انتبه لحالة الأحرف
        "langid":      "AR",
        "trackid":     trackid,
        "responseURL": success_url,  # ← انتبه لحالة الأحرف
        "errorURL":    fail_url,     # ← انتبه لحالة الأحرف
        "udf1": str(request.user.id) if request.user.is_authenticated else "",
        "udf2": "",
        "udf3": "",
        "udf4": "",
        "udf5": "",
    }

    trandata_enc_hex = encrypt_trandata(trandata_pairs)

    return render(request, "payments/rajhi_checkout.html", {
        "action_url": action_url,
        "id": tranportal_id,
        "password": tranportal_password,
        "trandata": trandata_enc_hex,
        "debug": request.GET.get("debug") == "1",
        "debug_plain": f"env={'UAT' if use_uat else 'PROD'}\ntrackid={trackid}\ntrandata_plain={trandata_pairs}\ntrandata_hex_len={len(trandata_enc_hex)}",
    })


@login_required
def rajhi_hosted_start(request, package_id):
    """
    خطوة Hosted (REST):
    - يبني trandata (AES-CBC) بالقيم المطلوبة
    - يرسل JSON إلى /pg/payment/hosted.htm
    - يعيد توجيه المستخدم إلى paymentpage.htm?PaymentID=...
    """
    package = get_object_or_404(GamePackage, id=package_id, is_active=True)
    if package.is_free:
        messages.info(request, "هذه حزمة مجانية — لا حاجة للدفع.")
        return redirect("games:letters_home")

    # منع شراء نشط مكرر
    existing = UserPurchase.objects.filter(user=request.user, package=package, is_completed=False).first()
    if existing:
        messages.warning(request, "لديك هذه الحزمة بالفعل.")
        return redirect("games:letters_home")

    cfg = getattr(settings, "RAJHI_CONFIG", {})
    tranportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
    tranportal_pw = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()
    if not tranportal_id or not tranportal_pw:
        messages.error(request, "إعدادات بوابة الراجحي ناقصة (TRANSPORTAL_ID / PASSWORD).")
        return redirect("payments:cancel")

    # روابط رجوع مطلقة وبروتوكول HTTPS
    base_cb = (os.environ.get("PUBLIC_BASE_URL") or request.build_absolute_uri("/").rstrip("/")).rstrip("/")
    if base_cb.startswith("http://"):
        base_cb = "https://" + base_cb[len("http://"):]
    success_url = urljoin(base_cb + "/", "payments/rajhi/callback/success/")
    fail_url    = urljoin(base_cb + "/", "payments/rajhi/callback/fail/")

    # أنشئ Transaction معلّقة
    txn = Transaction.objects.create(
        user=request.user,
        package=package,
        amount=package.price,
        payment_method=PaymentMethod.objects.filter(is_active=True).first(),
        status="pending",
        notes="Hosted-Init",
    )

    # trackid قصير كما يفضّله مزوّد البوابة
    trackid = get_random_string(12, allowed_chars="0123456789")

    # ⚠️ المفاتيح داخل trandata بالقيم/حالة الأحرف المطلوبة في الدليل
    trandata_pairs = {
        "id":           tranportal_id,          # داخل التشفير
        "password":     tranportal_pw,          # داخل التشفير
        "action":       "1",                    # 1 = Purchase
        "currencyCode": "682",                  # SAR
        "responseURL":  success_url,            # داخل التشفير
        "errorURL":     fail_url,               # داخل التشفير
        "trackId":      trackid,
        "amt":          f"{package.price:.2f}",
        "langid":       "AR",
        # تمرير مراجع اختيارية
        "udf1": str(request.user.id),
        "udf2": str(txn.id),   # نربط ردّ الراجحي بالمعاملة
        "udf3": "",
        "udf4": "",
        "udf5": "",
    }

    try:
        trandata_hex = encrypt_trandata(trandata_pairs)
    except Exception as e:
        logger.error("encrypt_trandata failed: %s", e)
        messages.error(request, "تعذر تجهيز بيانات الدفع.")
        return redirect("payments:cancel")

    # Endpoint الرسمي للـ Hosted (REST)
    endpoint = "https://securepayments.alrajhibank.com.sa/pg/payment/hosted.htm"
    payload = [{
        "id": tranportal_id,
        "trandata": trandata_hex,
        "responseURL": success_url,
        "errorURL": fail_url,
    }]
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/html;q=0.8",
    }

    try:
        resp = requests.post(endpoint, data=json.dumps(payload), headers=headers, timeout=30, verify=True)
    except requests.RequestException as e:
        logger.error("Hosted POST error: %s", e)
        messages.error(request, "تعذر الوصول لبوابة الدفع.")
        return redirect("payments:cancel")

    if resp.status_code != 200 or not resp.text.strip():
        logger.error("Hosted bad status/body: %s %s", resp.status_code, resp.text[:200])
        messages.error(request, "استجابة غير متوقعة من بوابة الدفع.")
        return redirect("payments:cancel")

    try:
        data = resp.json()
        rec = data[0] if isinstance(data, list) and data else {}
    except Exception:
        logger.warning("Hosted non-JSON response: %s", resp.text[:400])
        messages.error(request, "صيغة الرد غير متوقعة.")
        return redirect("payments:cancel")

    status = str(rec.get("status", "")).strip()
    result = str(rec.get("result", "")).strip()

    # مثال نجاح: "<PAYMENTID>:https://securepayments.alrajhibank.com.sa/pg/paymentpage.htm"
    if status == "1" and ":" in result:
        payment_id, base_url = result.split(":", 1)
        payment_id = payment_id.strip()
        redirect_url = f"{base_url.strip()}?PaymentID={payment_id}"

        # نحفظ بعض التفاصيل للتشخيص
        txn.gateway_transaction_id = payment_id
        txn.gateway_response = {"init_status": status, "init_result": result}
        txn.save(update_fields=["gateway_transaction_id", "gateway_response", "updated_at"])

        return redirect(redirect_url)

    # فشل
    logger.error("Hosted init failed: status=%s result=%s", status, result)
    txn.status = "failed"
    txn.failure_reason = f"Hosted init failed: {status} {result}"
    txn.save(update_fields=["status", "failure_reason", "updated_at"])
    messages.error(request, "فشلت تهيئة عملية الدفع. حاول لاحقًا.")
    return redirect("payments:cancel")
