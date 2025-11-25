# payments/telr.py

import uuid
from django.conf import settings

TELR_STORE_ID = "34132"
TELR_AUTH_KEY = "wT45z-TDzZ3@hvV"
TELR_TEST_MODE = "1"  # 1 = Test Mode

# دومينك الرسمي
BASE_URL = "https://wesh-aljawab.com"


def generate_telr_url(purchase, request):
    """
    إنشاء رابط الدفع عبر Telr: Hosted Payment Page
    """

    package = purchase.package

    # السعر الحقيقي
    amount = str(package.discounted_price or package.price)

    # Telr يحتاج رقم أمر فريد
    order_id = f"{purchase.id}"

    # == RETURN URL ==
    return_auth = f"{BASE_URL}/payments/telr/success/?purchase={purchase.id}"
    return_decl = f"{BASE_URL}/payments/telr/failed/?purchase={purchase.id}"
    return_cancl = f"{BASE_URL}/payments/telr/cancel/?purchase={purchase.id}"

    # == CALLBACK / WEBHOOK ==
    notify_url = f"{BASE_URL}/payments/telr/webhook/"

    # اسم الحزمة
    package_name = f"حزمة رقم {package.package_number}"

    payload = {
        "ivp_method": "create",
        "ivp_store": TELR_STORE_ID,
        "ivp_authkey": TELR_AUTH_KEY,
        "ivp_test": TELR_TEST_MODE,

        # معلومات الطلب
        "ivp_cart": order_id,
        "ivp_amount": amount,
        "ivp_currency": "SAR",
        "ivp_desc": package_name,

        # اللغة
        "ivp_lang": "ar",

        # == URLs ==
        "return_auth": return_auth,
        "return_decl": return_decl,
        "return_can": return_cancl,
        "ivp_callback": notify_url,

        # بيانات العميل
        "bill_email": request.user.email or "noemail@wesh-aljawab.com",
        "bill_fname": request.user.first_name or request.user.username,
    }

    endpoint = "https://secure.telr.com/gateway/order.json"

    return endpoint, payload
