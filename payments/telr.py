import hashlib
import uuid
from django.conf import settings

TELR_STORE_ID = "34132"
TELR_AUTH_KEY = "wT45z-TDzZ3@hvV"
TELR_TEST_MODE = "1"  # نخليه Test الآن، ونغيره 0 بعد التفعيل

BASE_URL = "https://wesh-aljawab.com"  # دومينك


def generate_telr_url(purchase, request):
    """
    إنشاء رابط الدفع باستخدام Hosted Payment Page
    """

    amount = str(purchase.package.price)

    order_id = str(uuid.uuid4())

    # return URLs
    return_auth = f"{BASE_URL}/payments/telr/success/?purchase={purchase.id}"
    return_decl = f"{BASE_URL}/payments/telr/failed/?purchase={purchase.id}"
    return_cancl = f"{BASE_URL}/payments/telr/cancel/?purchase={purchase.id}"

    payload = {
        "ivp_method": "create",
        "ivp_store": TELR_STORE_ID,
        "ivp_authkey": TELR_AUTH_KEY,
        "ivp_test": TELR_TEST_MODE,
        "ivp_lang": "ar",
        "ivp_cart": order_id,
        "ivp_amount": amount,
        "ivp_currency": "SAR",
        "ivp_desc": f"شراء حزمة {purchase.package.title}",

        "return_auth": return_auth,
        "return_decl": return_decl,
        "return_can": return_cancl,

        # بيانات العميل (اختياري لكنها مهمة)
        "bill_email": request.user.email,
        "bill_fname": request.user.first_name or request.user.username,
    }

    # نبني URL
    base = "https://secure.telr.com/gateway/order.json"

    return base, payload
