# payments/views.py
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.utils import timezone
from django.db import IntegrityError, transaction as db_txn
from django.views.decorators.csrf import csrf_exempt
from django.utils.crypto import get_random_string
from django.urls import reverse

from decimal import Decimal, InvalidOperation
import logging
import os
import json
import requests

from games.models import GamePackage, UserPurchase
from .models import Transaction, PaymentMethod, FakePaymentGateway, Invoice
from accounts.models import UserActivity

# تشفير trandata عبر وحدة الراجحي (لـ direct/checkout)
from .rajhi_crypto import encrypt_trandata, decrypt_trandata
# تشفير trandata الخاص بتكامل Bank Hosted (حسب ملف PDF)
from .utils_rajhi import encrypt_trandata_hosted

logger = logging.getLogger("payments.views")

# بوابات الراجحي القديمة (direct form init)
GATEWAY_URL_PROD = "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"
GATEWAY_URL_UAT  = "https://securepayments.alrajhibank.com.sa/pg/servlet/PaymentInitHTTPServlet?param=paymentInit"

# ------------------------------------------------------------------------------------
# صفحات عامة
# ------------------------------------------------------------------------------------
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

# ------------------------------------------------------------------------------------
# فحص إعدادات الراجحي
# ------------------------------------------------------------------------------------
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

# ------------------------------------------------------------------------------------
# Direct Init (نموذج تلقائي — لأغراض الاختبار فقط)
# ------------------------------------------------------------------------------------
def rajhi_direct_init(request):
    """
    يبني trandata بالقيم الصحيحة (ResponseURL / ErrorURL)
    ويرسل فورم مخفي لبوابة الراجحي (UAT أو PROD حسب ?env=uat).
    """
    env = (request.GET.get("env") or "").lower()
    is_uat = env == "uat"
    gateway_url = GATEWAY_URL_UAT if is_uat else GATEWAY_URL_PROD

    cfg = getattr(settings, "RAJHI_CONFIG", {})
    tranportal_id = cfg.get("TRANSPORTAL_ID", "").strip()
    tranportal_password = cfg.get("TRANSPORTAL_PASSWORD", "").strip()

    # نبني روابط مطلقة https للـ callbacks
    success_url = request.build_absolute_uri(reverse("payments:rajhi_callback_success"))
    fail_url    = request.build_absolute_uri(reverse("payments:rajhi_callback_fail"))

    # trackid عشوائي قصير
    trackid = get_random_string(12, allowed_chars="0123456789")

    # IMPORTANT: المفاتيح بحالة الأحرف كما تتوقع البوابة (نسخة الـ direct)
    trandata_pairs = {
        "action": "1",
        "amt": request.GET.get("amt", "5.00"),
        "currencycode": "682",
        "langid": "AR",
        "trackid": trackid,
        "ResponseURL": success_url,
        "ErrorURL": fail_url,
        "udf1": "",
        "udf2": "",
        "udf3": "",
        "udf4": "",
        "udf5": "",
    }

    trandata_hex = encrypt_trandata(trandata_pairs)

    context = {
        "gateway_url": gateway_url,
        "id": tranportal_id,
        "password": tranportal_password,
        "trandata": trandata_hex,
        "debug": (request.GET.get("debug") == "1"),
        "debug_plain": "&".join(f"{k}={v}" for k, v in trandata_pairs.items()),
    }
    return render(request, "payments/rajhi_direct_init.html", context)

# ------------------------------------------------------------------------------------
# Bank Hosted (حسب ملف PDF — REST)  ✅
# ------------------------------------------------------------------------------------
@login_required
def rajhi_hosted_start(request, package_id):
    """
    Bank Hosted (per PDF v1.24):
    - JSON POST إلى hosted.htm
    - داخل trandata: id,password,action,currencyCode,errorURL,responseURL,trackId,amt,langid,udf1..udf5
    - رد البوابة يحوي: result = "<PAYMENTID>:<paymentpage-url>", status="1"
    - نعيد توجيه العميل إلى: <paymentpage-url>?PaymentID=<PAYMENTID>
    """
    package = get_object_or_404(GamePackage, id=package_id, is_active=True)
    if package.is_free:
        messages.info(request, "هذه حزمة مجانية — لا حاجة للشراء.")
        return redirect("games:letters_home")

    # أنشئ معاملة pending
    txn = Transaction.objects.create(
        user=request.user,
        package=package,
        amount=package.price,
        payment_method=PaymentMethod.objects.filter(is_active=True).first(),
        status="pending",
    )

    cfg = getattr(settings, "RAJHI_CONFIG", {}) or {}
    tranportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
    tranportal_pw = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

    # روابط نجاح/فشل HTTPS مطلقة
    success_url = request.build_absolute_uri(reverse("payments:rajhi_callback_success"))
    fail_url    = request.build_absolute_uri(reverse("payments:rajhi_callback_fail"))

    # المبلغ بصيغة #.##
    amount = f"{package.price:.2f}"

    # === trandata (بالضبط كما في الدليل) ===
    trandata_pairs = {
        "id": tranportal_id,           # داخل التشفير
        "password": tranportal_pw,     # داخل التشفير
        "action": "1",                 # 1 = Purchase
        "currencyCode": "682",         # SAR
        "errorURL": fail_url,          # داخل التشفير
        "responseURL": success_url,    # داخل التشفير
        "trackId": str(txn.id),        # مرجع فريد
        "amt": amount,
        "langid": "AR",
        "udf1": str(request.user.id),
        "udf2": str(txn.id),
        "udf3": "",
        "udf4": "",
        "udf5": "",
    }

    try:
        trandata_hex = encrypt_trandata_hosted(trandata_pairs)
    except Exception as e:
        logger.error("Hosted encrypt error: %s", e)
        messages.error(request, "خطأ في تهيئة بيانات الدفع.")
        return redirect("payments:cancel")

    # Endpoint من الدليل (يمكن تغييره عبر متغير بيئة)
    endpoint = os.environ.get(
        "RAJHI_HOSTED_ENDPOINT",
        "https://securepayments.neoleap.com.sa/pg/payment/hosted.htm",
    )

    payload = [{
        "id": tranportal_id,
        "trandata": trandata_hex,
        "errorURL": fail_url,       # مذكور بالدليل كذلك في الجسم الخارجي
        "responseURL": success_url,
    }]

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/html;q=0.8",
    }

    try:
        resp = requests.post(endpoint, data=json.dumps(payload), headers=headers, timeout=45)
    except Exception as e:
        logger.error("Hosted POST exception: %s", e)
        messages.error(request, "تعذر الاتصال ببوابة الدفع.")
        return redirect("payments:cancel")

    if resp.status_code != 200:
        logger.error("Hosted HTTP %s, body=%s", resp.status_code, (resp.text or "")[:500])
        messages.error(request, "استجابة غير متوقعة من بوابة الدفع.")
        return redirect("payments:cancel")

    try:
        data = resp.json()
        rec = data[0] if isinstance(data, list) and data else {}
        result = str(rec.get("result", "")).strip()
        status = str(rec.get("status", "")).strip()
    except Exception as e:
        logger.error("Hosted JSON parse error: %s | body=%s", e, (resp.text or "")[:500])
        messages.error(request, "تعذر قراءة استجابة بوابة الدفع.")
        return redirect("payments:cancel")

    if status != "1" or ":" not in result:
        logger.error("Hosted bad result: status=%s result=%s", status, result)
        messages.error(request, "فشل الحصول على صفحة الدفع.")
        return redirect("payments:cancel")

    payment_id, url_base = result.split(":", 1)
    payment_id = payment_id.strip()
    url_base = url_base.strip()
    redirect_url = f"{url_base}?PaymentID={payment_id}"

    # توجيه العميل لصفحة الدفع
    return HttpResponseRedirect(redirect_url)

# ------------------------------------------------------------------------------------
# Callbacks (Success/Fail) — مع فك التشفير والتحقق
# ------------------------------------------------------------------------------------
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
    نجاح: فك trandata → idempotency → تحقق المبلغ → إتمام txn + إنشاء UserPurchase + UserActivity
    """
    plain, params = _extract_trandata(request)
    if not params:
        return HttpResponseBadRequest("Missing/invalid trandata")

    result    = (params.get("result") or "").upper()
    paymentid = params.get("paymentid") or params.get("paymentId") or ""
    trackid   = params.get("trackid") or params.get("trackId") or ""
    udf1_user = params.get("udf1") or ""
    udf2_txn  = params.get("udf2") or ""
    amt_str   = params.get("amt") or params.get("amount") or ""
    amt_dec   = _parse_decimal_safe(amt_str)

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
        txn = txn_qs.select_for_update().first()

        if txn.status == 'completed':
            logger.info("Callback success: idempotent hit (already completed). txn=%s", txn.id)
            return HttpResponse("Already completed", status=200)

        if result not in ("CAPTURED", "APPROVED", "SUCCESS", "SUCCESSFUL"):
            txn.status = 'failed'
            txn.failure_reason = params.get("errorText") or params.get("error") or "Transaction not approved"
            txn.gateway_response = params
            txn.save(update_fields=['status', 'failure_reason', 'gateway_response', 'updated_at'])
            logger.warning("Callback success endpoint with non-success result=%s txn=%s", result, txn.id)
            return HttpResponse("Not approved", status=200)

        if amt_dec is not None and txn.amount is not None and amt_dec != txn.amount:
            txn.status = 'failed'
            txn.failure_reason = f"Amount mismatch: got {amt_dec} expected {txn.amount}"
            txn.gateway_response = params
            txn.save(update_fields=['status', 'failure_reason', 'gateway_response', 'updated_at'])
            logger.error("Amount mismatch: txn=%s amt_cb=%s amt_txn=%s", txn.id, amt_dec, txn.amount)
            return HttpResponse("Amount mismatch", status=200)

        txn.status = 'completed'
        txn.completed_at = timezone.now()
        if paymentid:
            txn.gateway_transaction_id = paymentid
        txn.gateway_response = params
        txn.save(update_fields=['status', 'completed_at', 'gateway_transaction_id', 'gateway_response', 'updated_at'])

        try:
            UserPurchase.objects.get_or_create(user=txn.user, package=txn.package)
        except IntegrityError:
            pass

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
    فشل/إلغاء: نضبط txn=failed (idempotent) ونخزن سبب الفشل.
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

# ------------------------------------------------------------------------------------
# صفحة تجربة قديمة (ارسال trandata داخل النموذج المباشر)
# ------------------------------------------------------------------------------------
def rajhi_checkout(request):
    """
    صفحة اختبار: إرسال trandata مع Tranportal ID/Password للبوابة.
    """
    cfg = settings.RAJHI_CONFIG
    tranportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
    tranportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

    use_uat = (request.GET.get("env", "").lower() == "uat") or settings.DEBUG
    action_url = GATEWAY_URL_UAT if use_uat else GATEWAY_URL_PROD

    amount  = (request.GET.get("amt") or "3.00").strip()
    trackid = (request.GET.get("t") or get_random_string(12, allowed_chars="0123456789")).strip()

    base_cb = (os.environ.get("PUBLIC_BASE_URL") or request.build_absolute_uri('/').rstrip('/')).rstrip('/')
    if base_cb.startswith("http://"):
        base_cb = "https://" + base_cb[len("http://"):]
    success_url = f"{base_cb}/payments/rajhi/callback/success/"
    fail_url    = f"{base_cb}/payments/rajhi/callback/fail/"

    trandata_pairs = {
        "id":          tranportal_id,
        "password":    tranportal_password,
        "action":      "1",
        "amt":         amount,
        "currencyCode": "682",
        "langid":      "AR",
        "trackid":     trackid,
        "responseURL": success_url,
        "errorURL":    fail_url,
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
