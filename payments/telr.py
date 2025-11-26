# payments/telr.py

import uuid
from django.conf import settings

TELR_STORE_ID = "34132"
TELR_AUTH_KEY = "wT45z-TDzZ3@hvV"
TELR_TEST_MODE = "1"  # 1 = Test Mode

# دومينك الرسمي
BASE_URL = "https://wesh-aljawab.com"


# payments/telr.py

def generate_telr_url(purchase, request, order_id):
    """
    إنشاء رابط الدفع عبر Telr باستخدام order_id الحقيقي
    """

    package = purchase.package

    # السعر الحقيقي
    amount = str(package.discounted_price or package.price)

    BASE_URL = "https://wesh-aljawab.com"

    # == RETURN URL ==
    return_auth = f"{BASE_URL}/payments/telr/success/?purchase={purchase.id}"
    return_decl = f"{BASE_URL}/payments/telr/failed/?purchase={purchase.id}"
    return_cancl = f"{BASE_URL}/payments/telr/cancel/?purchase={purchase.id}"

    # == CALLBACK ==
    notify_url = f"{BASE_URL}/payments/telr/webhook/"

    package_name = f"حزمة رقم {package.package_number}"

    payload = {
        "ivp_method": "create",
        "ivp_store": "34132",
        "ivp_authkey": "wT45z-TDzZ3@hvV",
        "ivp_test": "1",

        # أهم نقطة — رقم الطلب الحقيقي
        "ivp_cart": order_id,

        "ivp_amount": amount,
        "ivp_currency": "SAR",
        "ivp_desc": package_name,
        "ivp_lang": "ar",

        # URLs
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
