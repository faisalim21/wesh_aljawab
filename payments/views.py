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

logger = logging.getLogger(__name__)

API_URL = "https://securepayments.alrajhibank.com.sa/PGMerchantPayment"

@login_required
def create_payment(request, package_id):
    """بناء جلسة دفع وتحويل العميل لبوابة الراجحي"""
    try:
        package = GamePackage.objects.get(pk=package_id, is_active=True)
    except GamePackage.DoesNotExist:
        return HttpResponseBadRequest("الحزمة غير موجودة.")

    amount = str(package.effective_price)
    track_id = f"{request.user.id}-{package.id}"

    plain = {
        "id": settings.RAJHI_CONFIG["TRANSPORTAL_ID"],
        "password": settings.RAJHI_CONFIG["TRANSPORTAL_PASSWORD"],
        "action": "1",  # عملية شراء
        "currencyCode": "682",
        "amt": amount,
        "trackId": track_id,
        "responseURL": request.build_absolute_uri("/payments/return/"),
        "errorURL": request.build_absolute_uri("/payments/return/"),
        "udf1": str(request.user.id),
        "udf2": str(package.id),
    }

    trandata = encrypt_trandata(plain)
    payload = {
        "id": settings.RAJHI_CONFIG["TRANSPORTAL_ID"],
        "trandata": trandata,
        "responseURL": plain["responseURL"],
        "errorURL": plain["errorURL"],
    }

    # نطلب Session من الراجحي
    r = requests.post(API_URL, data=payload, timeout=20)
    if r.status_code != 200:
        logger.error("AlRajhi error: %s", r.text)
        return HttpResponse("فشل الاتصال ببوابة الدفع.", status=500)

    resp = r.json()
    if resp.get("status") != "1":
        logger.error("AlRajhi reject: %s", resp)
        return HttpResponse("فشل إنشاء الدفع.", status=400)

    payment_url = resp["result"].split(":")[1]
    return redirect(payment_url)


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
