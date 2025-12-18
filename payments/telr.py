# payments/telr.py

from django.conf import settings
from decimal import Decimal, ROUND_HALF_UP


BASE_URL = "https://wesh-aljawab.com"


def _format_amount(amount):
    """
    Telr يتطلب رقم بصيغة 0.00
    """
    return str(
        Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def generate_telr_url(purchase, request, order_id):
    """
    إنشاء رابط الدفع عبر Telr (Live / Test) بطريقة آمنة ومطابقة
    """

    package = purchase.package

    # ✅ السعر الحقيقي
    amount = _format_amount(package.effective_price)

    game_type = package.game_type

    return_auth = (
        f"{BASE_URL}/payments/telr/success/"
        f"?purchase={purchase.id}&type={game_type}"
    )
    return_decl = (
        f"{BASE_URL}/payments/telr/failed/"
        f"?purchase={purchase.id}&type={game_type}"
    )
    return_cancl = (
        f"{BASE_URL}/payments/telr/cancel/"
        f"?purchase={purchase.id}&type={game_type}"
    )

    notify_url = f"{BASE_URL}/payments/telr/webhook/"

    payload = {
        "ivp_method": "create",
        "ivp_store": settings.TELR_STORE_ID,
        "ivp_authkey": settings.TELR_AUTH_KEY,
        "ivp_test": "1" if settings.TELR_TEST_MODE else "0",

        # رقم الطلب
        "ivp_cart": order_id,

        "ivp_amount": amount,
        "ivp_currency": "SAR",
        "ivp_desc": f"حزمة رقم {package.package_number}",
        "ivp_lang": "ar",

        # Return URLs
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
