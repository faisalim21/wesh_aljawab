# payments/views.py
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.db import IntegrityError, transaction as db_txn
from django.db.models import F
from django.views.decorators.csrf import csrf_exempt
from django.utils.crypto import get_random_string

from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
import logging
import os

from games.models import GamePackage, UserPurchase
from .models import Transaction, PaymentMethod, FakePaymentGateway, Invoice
from accounts.models import UserActivity

# ✅ توحيد التشفير على AES/3DES عبر rajhi_crypto (الافتراضي الآن AES في الإعدادات المقترحة)
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
    شراء حزمة (ما زال يستخدم بوابة وهمية لأغراض التطوير).
    لاحقًا يمكن تحويله لتدفق الراجحي بنفس منطق rajhi_direct_init.
    """
    package = get_object_or_404(GamePackage, id=package_id, is_active=True)

    if package.is_free:
        messages.info(request, 'هذه حزمة مجانية — لا حاجة للشراء.')
        return redirect('games:letters_home')

    existing_purchase = UserPurchase.objects.filter(
        user=request.user, package=package, is_completed=False
    ).first()
    if existing_purchase:
        messages.warning(request, 'لديك هذه الحزمة بالفعل!')
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

            messages.success(request, 'تم الشراء بنجاح! يمكنك الآن اللعب 🎉')
            return redirect('games:letters_home')
        else:
            messages.error(request, 'فشل في عملية الدفع، يرجى المحاولة مرة أخرى')
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
    return JsonResponse({
        "ok": len(missing) == 0 and (resource_exists or bool(cfg.get("RESOURCE_KEY"))),
        "missing_keys": missing,
        "config_preview": safe_cfg,
    })


# =========================
#  تدفّق التهيئة المباشر (المعتمد)
# =========================
def rajhi_direct_init(request):
    """
    إرسال id + password + trandata(AES/3DES) إلى بوابة الراجحي.
    يدعم ?env=uat لاختيار UAT حتى لو DEBUG=False
    ويدعم ?pkg=<uuid> لإنشاء Transaction مربوط بالحزمة قبل التحويل.
    """
    cfg = settings.RAJHI_CONFIG
    tranportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
    tranportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

    if not tranportal_id or not tranportal_password:
        debug_text = (
            "ERROR: Missing config:\n"
            f"TRANSPORTAL_ID set? {bool(tranportal_id)}\n"
            f"TRANSPORTAL_PASSWORD set? {bool(tranportal_password)}\n"
            "تحقق من .env"
        )
        return render(request, "payments/rajhi_direct_init.html", {
            "gateway_url": GATEWAY_URL_PROD,
            "id": tranportal_id, "password": tranportal_password, "trandata": "",
            "debug": True, "debug_plain": debug_text,
        })

    # اختيار البيئة
    use_uat = (request.GET.get("env", "").lower() == "uat") or settings.DEBUG
    action_url = GATEWAY_URL_UAT if use_uat else GATEWAY_URL_PROD

    # قيم
    amount  = (request.GET.get("amt") or "3.00").strip()
    trackid = (request.GET.get("t") or get_random_string(12, allowed_chars="0123456789")).strip()

    # (اختياري) ربط بحزمة وإنشاء Transaction قبل التحويل
    txn = None
    pkg_id = request.GET.get("pkg")
    if pkg_id:
        try:
            pkg = GamePackage.objects.get(id=pkg_id, is_active=True)
            # لو ما مررت amt نأخذ سعر الحزمة
            amount = f"{pkg.effective_price:.2f}"
            if request.user.is_authenticated:
                txn = Transaction.objects.create(
                    user=request.user, package=pkg, amount=Decimal(amount),
                    payment_method=None, status='pending', notes=f"trackid={trackid}"
                )
        except GamePackage.DoesNotExist:
            logger.warning("rajhi_direct_init: package not found or inactive: %s", pkg_id)

    # استخدم دومين عام إن توفر، واحرص أن يكون HTTPS
    base_cb = (os.environ.get("PUBLIC_BASE_URL") or request.build_absolute_uri('/').rstrip('/')).rstrip('/')
    if base_cb.startswith("http://"):
        base_cb = "https://" + base_cb[len("http://"):]

    trandata_pairs = {
        "action":        "1",
        "amt":           amount,
        "currencycode":  "682",
        "langid":        "AR",
        "trackid":       trackid,
        "responseURL":   f"{base_cb}/payments/rajhi/callback/success/",
        "errorURL":      f"{base_cb}/payments/rajhi/callback/fail/",

        "udf1":          str(request.user.id) if request.user.is_authenticated else "",
        "udf2":          (str(txn.id) if txn else ""),  # نخزن id المعاملة - يفيد بالمطابقة
        "udf3":          "",
        "udf4":          "",
        "udf5":          "",
    }
    trandata_enc_hex = encrypt_trandata(trandata_pairs)

    context = {
        "gateway_url": action_url,
        "id": tranportal_id,
        "password": tranportal_password,
        "trandata": trandata_enc_hex,
        "debug": request.GET.get("debug") == "1",
        "debug_plain": (
            f"env={'UAT' if use_uat else 'PROD'}\n"
            f"trackid={trackid}\n"
            f"trandata_plain={urlencode(trandata_pairs)}\n"
            f"trandata_hex_len={len(trandata_enc_hex)}"
        ),
    }
    logger.debug("RAJHI_INIT: prepared trandata length=%s env=%s", len(trandata_enc_hex), "UAT" if use_uat else "PROD")
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
    txn_qs = None
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
            # قد يكون تم إنشاؤه بالفعل بسبب قيود unique على الشراء النشط
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
        txn_qs = None
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
#  صفحة تجربة قديمة (تم تحويلها لتستخدم trandata أيضًا)
# =========================
def rajhi_checkout(request):
    """
    صفحة عرض زر "ادفع الآن" لكن تُرسل trandata الموحّدة (بدل تمرير الحقول الخام).
    """
    cfg = settings.RAJHI_CONFIG
    tranportal_id = (cfg.get("TRANSPORTAL_ID") or "").strip()
    tranportal_password = (cfg.get("TRANSPORTAL_PASSWORD") or "").strip()

    if not tranportal_id or not tranportal_password:
        return render(request, "payments/rajhi_checkout.html", {
            "action_url": GATEWAY_URL_PROD if not settings.DEBUG else GATEWAY_URL_UAT,
            "id": tranportal_id, "password": tranportal_password, "trandata": "",
            "debug": True, "debug_plain": "التهيئة ناقصة (tranportal id/password)."
        })

    amount  = (request.GET.get("amt") or "3.00").strip()
    trackid = (request.GET.get("t") or get_random_string(12, allowed_chars="0123456789")).strip()

    # استخدم دومين عام إن توفر، واحرص أن يكون HTTPS
    base_cb = (os.environ.get("PUBLIC_BASE_URL") or request.build_absolute_uri('/').rstrip('/')).rstrip('/')
    if base_cb.startswith("http://"):
        base_cb = "https://" + base_cb[len("http://"):]

    trandata_pairs = {
        "action":        "1",
        "amt":           amount,
        "currencycode":  "682",
        "langid":        "AR",
        "trackid":       trackid,
        "responseURL":   f"{base_cb}/payments/rajhi/callback/success/",
        "errorURL":      f"{base_cb}/payments/rajhi/callback/fail/",

        "udf1":          str(request.user.id) if request.user.is_authenticated else "",
        "udf2":          "",
        "udf3":          "",
        "udf4":          "",
        "udf5":          "",
    }
    trandata_enc_hex = encrypt_trandata(trandata_pairs)

    return render(request, "payments/rajhi_checkout.html", {
        "action_url": GATEWAY_URL_PROD if not settings.DEBUG else GATEWAY_URL_UAT,
        "id": tranportal_id,
        "password": tranportal_password,
        "trandata": trandata_enc_hex,
        "debug": request.GET.get("debug") == "1",
        "debug_plain": f"trackid={trackid}"
    })
