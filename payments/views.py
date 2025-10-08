# payments/views.py
import logging
import requests
from django.shortcuts import redirect
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.contrib.auth.decorators import login_required
from games.models import GamePackage, UserPurchase
from .utils_rajhi import encrypt_trandata, decrypt_trandata
from django.urls import reverse
from .utils_rajhi import get_trandata_encrypted  # تأكد أن هذه الدالة موجودة وتشفّر البيانات

logger = logging.getLogger(__name__)

def create_payment(request, package_id):
    """إنشاء طلب دفع وتحويل العميل إلى صفحة بوابة الراجحي"""
    try:
        # ===== بيانات الطلب =====
        response_url = request.build_absolute_uri(reverse("payments:success"))
        error_url = request.build_absolute_uri(reverse("payments:failure"))

        trandata_plain = {
            "id": settings.RAJHI_CONFIG["TRANSPORTAL_ID"],
            "password": settings.RAJHI_CONFIG["TRANSPORTAL_PASSWORD"],
            "action": "1",  # Purchase
            "currencyCode": "682",  # SAR
            "amt": "10.00",  # للتجربة، عدلها حسب package.effective_price
            "responseURL": response_url,
            "errorURL": error_url,
            "trackId": "TEST12345",
        }

        # ===== تشفير البيانات =====
        encrypted_trandata = get_trandata_encrypted(trandata_plain)

        payload = {
            "id": settings.RAJHI_CONFIG["TRANSPORTAL_ID"],
            "trandata": encrypted_trandata,
            "responseURL": response_url,
            "errorURL": error_url,
        }

        API_URL = "https://securepayments.alrajhibank.com.sa/PGMerchantPayment"

        logger.debug("Sending payload to AlRajhi: %s", payload)

        r = requests.post(API_URL, data=payload, timeout=20, verify=True)

        logger.debug("Response status: %s", r.status_code)
        logger.debug("Response text: %s", r.text)

        if r.status_code == 200:
            logger.debug("Bank response: %s", r.text)
            if "https" in r.text:
                return redirect(r.text.strip())
            else:
                return HttpResponse("رد البنك غير متوقع: " + r.text, status=500)
        else:
            logger.error("Bank error %s: %s", r.status_code, r.text)
            return HttpResponse(f"فشل من البوابة (Status {r.status_code})", status=500)


    except Exception as e:
        logger.exception("Unexpected error in create_payment")
        return HttpResponse("خطأ داخلي في الدفع", status=500)


@csrf_exempt
def payment_return(request):
    """استقبال الرد النهائي بعد الدفع"""
    trandata = request.POST.get("trandata")
    if not trandata:
        return HttpResponseBadRequest("لا يوجد trandata.")

    data = decrypt_trandata(trandata)
    logger.info("Final response: %s", data)

    result = data.get("result")
    user_id = data.get("udf1")
    package_id = data.get("udf2")

    if result in ("CAPTURED", "APPROVED"):
        try:
            user = settings.AUTH_USER_MODEL.objects.get(pk=user_id)
            package = GamePackage.objects.get(pk=package_id)
            UserPurchase.objects.get_or_create(user=user, package=package)
        except Exception as e:
            logger.error("Error creating UserPurchase: %s", e)
    else:
        logger.warning("Payment failed: %s", result)

    return JsonResponse(data)


@csrf_exempt
def payment_webhook(request):
    """Webhook من الراجحي (للتأكيد النهائي)"""
    try:
        body = request.body.decode("utf-8")
    except Exception:
        return HttpResponseBadRequest("Invalid body")

    try:
        data = decrypt_trandata(body)
    except Exception:
        return HttpResponseBadRequest("Invalid trandata")

    logger.info("Webhook: %s", data)

    if data.get("result") in ("CAPTURED", "APPROVED"):
        try:
            user_id = data.get("udf1")
            package_id = data.get("udf2")
            user = settings.AUTH_USER_MODEL.objects.get(pk=user_id)
            package = GamePackage.objects.get(pk=package_id)
            UserPurchase.objects.get_or_create(user=user, package=package)
        except Exception as e:
            logger.error("Webhook create UserPurchase failed: %s", e)

    return JsonResponse({"status": "1"})
